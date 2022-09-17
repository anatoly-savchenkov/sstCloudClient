from __future__ import print_function
import requests
import json
import time
import codecs
import sys
from datetime import datetime
from deepdiff import DeepDiff

name = "sstCloudClient"
DEBUG = True;

# Hierarchy of entities:
# User can have multiple houses
#    Each house can have multiple devices
#       Each device can have sensors and counters

class SstCloudClient:
    def __init__(self, username, password):
        self.email = username
        self.username = username
        self.password = password
        self.user_data = None
        self.full_data = None 
        self.houses_data = None 
        self.lastRefresh = datetime.now() 
        self.dataChanged = False
        self.session = None
        self.headers = {'content-type':'application/json', 'Accept': 'application/json'}

    def release_session(self):
        if self.session:
            self.session.close()
            self.session = None

    def _do_get_request(self, url):
        if self.session is None:
            self.session = requests.Session()

        response = self.session.get(url, headers=self.headers, cookies=self.user_data, stream=False)
        if DEBUG is True:
           print(response.request.url)
           print(response.request.body)
           print(response.request.headers)
           print(response.url)
           print(response.text)
           print(response.headers)

        if response.status_code != requests.codes.ok:
            response.raise_for_status()

        return response.json()

    def _do_post_request(self, url, data):
        if self.session is None:
            self.session = requests.Session()

        response = self.session.post(url, json=data, headers=self.headers, cookies=self.user_data, stream=False)
        if DEBUG is True:
           print(response.request.url)
           print(response.request.body)
           print(response.request.headers)
           print(response.url)
           print(response.text)
           print(response.headers)

        if response.status_code != requests.codes.ok:
            response.raise_for_status()

        return response

    def _populate_full_data(self, force_refresh=False):
        if self.full_data is None or force_refresh:
            try:
                self._login()
                self._populate_houses_info(force_refresh)
                self.dataChanged = False

                for house in self.houses_data:
                    full_data = dict()
                    full_data[house['id']] = dict() 
                    full_data[house['id']]['House'] = house
                    full_data[house['id']]['wired_sensor'] = list()
                    full_data[house['id']]['wireless_sensor'] = list()
                    full_data[house['id']]['water_counter'] = list()
                    full_data[house['id']]['Devices'] = list()

                    url = 'https://api.sst-cloud.com/houses/%s/devices/' % (house['id'])
                    response = self._do_get_request(url)

                    for device in response:
                        # Process Neptun devices only.
                        # type: 0 - mc300, 1 - mc350, 2 - Neptun
                        if device['type'] != 2: continue

                        device['parsed_configuration'] = json.loads(device['parsed_configuration'])
                        full_data[house['id']]['Devices'].append(device)
                        _device_line = device['parsed_configuration']['settings']['lines_in']
                        index = 0;
                        for sensor, sensorType in _device_line.items():
                            if sensorType == "wired_sensor" and device['lines_enabled'][index] == True:
                                # device['parsed_configuration']['lines_status'][sensor] can be:
                                #  'off' - which means no alarm
                                #  'on'  - meaning wired sensor alarm
                                #  'blinking' - meaning alarm of a wireless sensor attached to the respecitve channel
                                # Here we ignore 'blinking' state and handle errors coming from wireless sensors inside 
                                # the special routine
                                _sensor = {
                                        'name': device['line_names'][index],
                                        'id' : index + 1,
                                        'deviceid': device['id'],
                                        'houseid' : house['id'],
                                        'value' : 'on' if device['parsed_configuration']['lines_status'][sensor] == 'on' else 'off',
                                        }
                                full_data[house['id']]['wired_sensor'].append(_sensor)
                            index += 1

                        for counter in self._get_house_dev_counters(house['id'],device['id']):
                           full_data[house['id']]['water_counter'].append(counter)

                        if device['parsed_configuration']['settings']['sensors_count'] > 0:
                           for sensor in self._get_house_dev_wireless_sensors(house['id'],device['id']):
                              full_data[house['id']]['wireless_sensor'].append(sensor)

                if self.full_data != full_data:
                    ddiff = DeepDiff(self.full_data, full_data, ignore_order=True)
                    print (ddiff)
                    self.full_data = full_data
                    self.dataChanged = True
                    self.lastRefresh = datetime.now()

            except Exception as e:
                print('Unable to communicate with server: ' + str(e))
                if self.full_data is None: raise
                # Unable to communicate with the server
                # Mark all devices offline in the cached state
                _is_changed = False
                for house in self.houseData():
                    for device in self.deviceData(house["id"]):
                        if device['is_connected'] == True:
                            device['is_connected'] = False
                            _is_changed = True
                if _is_changed == True:
                    self.dataChanged = True
                    self.lastRefresh = datetime.now()


    def _get_house_dev_counters(self, houseid, deviceid):
        url = "https://api.sst-cloud.com/houses/%s/devices/%s/counters" % (houseid,deviceid)
        counters = self._do_get_request(url)

        for counter in counters:
            counter['deviceid'] = counter['device']
            del counter['device']
            counter['houseid'] = houseid 

        return counters

    def _get_house_dev_wireless_sensors(self, houseid, deviceid):
        url = "https://api.sst-cloud.com/houses/%s/devices/%s/wireless_sensors" % (houseid,deviceid)
        sensors = self._do_get_request(url)
        index = 0;
        for sensor in sensors:
            sensor['id'] = index + 1
            index += 1
            sensor['deviceid'] = deviceid
            sensor['houseid'] = houseid 
            # I saw values above 100 (e.g. 250, 254) in case of a heavily dischanged battery
            sensor['battery_level'] = sensor['battery'] if sensor['battery'] <= 100 and sensor['battery'] >= 0 else 0
            del sensor['battery']
            sensor['value'] = 'off' if sensor['attention'] == False else 'on'
            del sensor['attention']

        return sensors


    def _get_house_dev_lines(self, houseid, deviceid):
        url = "https://api.sst-cloud.com/houses/%s/devices/%s/lines_status" % (houseid,deviceid)
        status = self._do_get_request(url)

        #TODO: Populate internal stuctures from status dictonary


    def _populate_houses_info(self, force_refresh=False):
        if self.houses_data is None or force_refresh:
            url = 'https://api.sst-cloud.com/houses/'

            self.houses_data = self._do_get_request(url)

            if len(self.houses_data) > 1:
                raise Exception("More than one house available")

    def _login(self):
        expires = None
        if self.user_data:
            for cookie in self.user_data:
               if cookie.name == 'sessionid':
                  expires = cookie.expires

        if self.user_data is None or expires < (time.time() + 10):
            url = 'https://api.sst-cloud.com/auth/login/'
            data = {'username':self.username,'password':self.password,'email':self.email,'language':'ru'}
            response = self._do_post_request(url, data)

#            print(response.json()['detail']) # TODO: Improve bad login handling

            # Cache session authentication data
            self.user_data = response.cookies
            self.headers['X-CSRFToken'] = self.user_data['csrftoken']
        return self.user_data

# TODO: Incomplete routine
    def _get_house_statistics(self, houseid):
        date_from = "2021-01-01"
        date_to   = "2021-02-19"
        interval = "day" #hour, day or month
        url = "https://api.sst-cloud.com/statistic/houses/%s/?date_from=%s&date_to=%s&interval=%s" % (houseid, date_from, date_to, interval)
        return self._do_get_request(url)

    def test(self):
        self._populate_full_data()
        print(json.dumps(self.houses_data,sort_keys=True,indent=2,ensure_ascii=False))
        print(json.dumps(self.full_data,sort_keys=True,indent=2,ensure_ascii=False))

    def houseData(self, force_refresh=False):
        if self.houses_data is None or force_refresh:
            self._login()
            self._populate_houses_info(force_refresh)

        for house in self.houses_data:
            yield house

    def deviceData(self, houseid, force_refresh=False):
        self._populate_full_data(force_refresh)
        house = self.full_data[houseid]

        for device in house['Devices']:
            yield device

    def deviceById(self, houseid, deviceid):
        house = self.full_data[houseid]

        for device in house['Devices']:
            if device['id'] == deviceid: return device

        return None

    def waterCounters(self, houseid, force_refresh=False):
        self._populate_full_data(force_refresh)
        house = self.full_data[houseid]
        for counter in house['water_counter']:
            yield counter

    def wiredSensors(self, houseid, force_refresh=False):
        self._populate_full_data(force_refresh)
        house = self.full_data[houseid]
        for sensor in house['wired_sensor']:
            yield sensor 

    def status(self, houseid, force_refresh=False):
        self._populate_full_data(force_refresh)
        house = self.full_data[houseid]
        status = list()
        for device in house['Devices']:
            status.append({
                'name':device['name'],
                'houseid':device['house'],
                'deviceid':device['id'],
                'close_valve_flag':device['parsed_configuration']['settings']['close_valve_flag'],
                'dry_flag':device['parsed_configuration']['settings']['dry_flag'],
                'valve_settings':device['parsed_configuration']['settings']['valve_settings'],
                'signal_level':device['parsed_configuration']['signal_level'],
                'alert':device['parsed_configuration']['settings']['status']['alert'],
                'dry_flag':device['parsed_configuration']['settings']['status']['dry_flag'],
                'sensors_lost':device['parsed_configuration']['settings']['status']['sensors_lost']
                })
        return status

    def setValve(self, houseid, deviceid, value=False):
        url = 'https://api.sst-cloud.com/houses/%s/devices/%s/valve_settings/' % (houseid, deviceid)
        data = {
                'valve_settings':'opened' if value else 'closed'
                }
        self._do_post_request(url, data)
        self._populate_full_data(True)

    def setValveOpen(self, houseid, deviceid):
        self.setValve(houseid,deviceid,True)

    def setValveClosed(self, houseid, deviceid):
        self.setValve(houseid,deviceid,False)

    def getValve(self, houseid, deviceid):
        self._populate_full_data()
        house = self.full_data[houseid]
        for device in house['Devices']:
            if device['id'] == deviceid:
                return device['parsed_configuration']['settings']['valve_settings']
        return None

    def setDryFlag(self, houseid, deviceid, value=False):
        url = 'https://api.sst-cloud.com/houses/%s/devices/%s/dry_flag/' % (houseid, deviceid)
        data = {
                'dry_flag' : 'on' if value else 'off'
                }
        self._do_post_request(url, data)
        self._populate_full_data(True)

    def setDryOn(self, houseid, deviceid):
        self.setDryFlag(houseid, deviceid, True)

    def setDryOff(self, houseid, deviceid):
        self.setDryFlag(houseid, deviceid, False)
