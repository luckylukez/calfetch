import json
import logging
import os
from datetime import date, timedelta, time

import requests
from dateutil import parser
import pytz
from flask import Blueprint, make_response, request, jsonify
from icalendar import Calendar, Event, vDatetime
from icalendar.prop import vCategory, vText

from . import eventor_utils
from . import google_utils
#from calfetch import request_handler
from common import check_api_key, KnownError
from definitions import config, ROOT_DIR

calendarfeeds_app = Blueprint('calendarfeeds', __name__)

timezone = pytz.timezone('Europe/Stockholm')


def add_activities(root, calendar: Calendar, config):
    for activity in root:
        try:
            attributes = activity.attrib
            cal_event = Event()

            cal_event['summary'] = '{} [{} anmälda]'.format(activity.find('Name').text, attributes['registrationCount'])

            if 'startTime' not in attributes:
                continue

            starttime = parser.parse(attributes['startTime'])
            starttime = starttime.astimezone(timezone)
            cal_event['dtstart'] = vDatetime(starttime).to_ical()

            if starttime.time() == time(0, 0, 0):
                endtime = starttime + timedelta(days=1)
            else:
                endtime = starttime + timedelta(hours=3)
            cal_event['dtend'] = vDatetime(endtime).to_ical()

            cal_event['categories'] = ','.join(['Eventor', 'Klubbaktivitet'])

            cal_event['description'] = config['Messages']['eventor_import'] + ' ' + config['Messages'][
                'original_ref'] + ' ' + attributes['url']

            cal_event['url'] = attributes['url']

            cal_event['uid'] = 'Activity_' + attributes['id'] + '@' + config['EventorApi']['base_url']

            calendar.add_component(cal_event)
        except RuntimeError as err:
            logging.warning(err)
            continue


def is_cancelled(event):
    if 'EventStatusId' in [t.tag for t in event.iter()]:
        return event.find('EventStatusId').text == config['Calendar']['cancelled_status_id']
    return False


def add_events(root, calendar: Calendar, config):
    for event in root:
        try:
            cal_event = Event()

            name = event.find('Name').text
            if is_cancelled(event):
                name = '[INSTÄLLD] ' + name

            org_id = event.find('Organiser').find('OrganisationId').text
            org_name = eventor_utils.org_name(org_id)
            cal_event['summary'] = '{}, {}'.format(name, org_name)

            startdate_str = event.find('StartDate').find('Date').text
            starttime_str = event.find('StartDate').find('Clock').text
            startdatetime = parser.parse(startdate_str + ' ' + starttime_str)
            startdatetime = timezone.localize(startdatetime)

            enddate_str = event.find('FinishDate').find('Date').text
            endtime_str = event.find('FinishDate').find('Clock').text
            enddatetime = parser.parse(enddate_str + ' ' + endtime_str)
            enddatetime = timezone.localize(enddatetime)

            if startdatetime == enddatetime:
                if startdatetime.time() == time(0, 0, 0, tzinfo=timezone):
                    enddatetime = startdatetime + timedelta(days=1)
                else:
                    enddatetime = startdatetime + timedelta(hours=3)

            elif startdatetime.date() != enddatetime.date() and enddatetime.time() == time(0, 0, 0, tzinfo=timezone):
                enddatetime += timedelta(days=1)

            cal_event.add('dtstart', startdatetime)

            cal_event.add('dtend', enddatetime)

            classification = config['EventClassification'][str(event.find('EventClassificationId').text)]
            cal_event['categories'] = ','.join(['Eventor', classification])

            url = config['EventorApi']['event_base_url'] + '/' + event.find('EventId').text
            cal_event['url'] = url

            cal_event['description'] = config['Messages']['eventor_import'] + ' ' + config['Messages'][
                'original_ref'] + ' ' + url

            cal_event['uid'] = 'Event_' + event.find('EventId').text + '@' + config['EventorApi']['base_url']

            calendar.add_component(cal_event)
        except RuntimeError as err:
            logging.warning(err)
            continue


def add_idrottonline_feeds(calendar: Calendar):
    try:
        with open(ROOT_DIR + '/idrottonline_feeds.json', "r") as json_file:
            data = json.load(json_file)

        for feed in data:
            feed_calendar = Calendar.from_ical(requests.get(feed['url']).text)
            calendar_name = vText.from_ical(feed_calendar['X-WR-CALNAME'])
            for component in feed_calendar.subcomponents:
                if 'categories' in component:
                    old_categories = [vCategory.from_ical(c)[0] for c in component['categories'].cats if
                                      vCategory.from_ical(c)[0] != '"']
                    new_categories = feed['categories'] + old_categories
                    component['categories'] = ','.join(new_categories)
                else:
                    component['categories'] = ','.join(feed_calendar['categories'])

                idrottonline_id = vText.from_ical(component['UID']).split('Activity')[1].split('@')[0]

                if 'description' in component:
                    description = vText.from_ical(component['description'])
                    description = description.replace('[', '<').replace(']', '>')
                else:
                    description = ''
                component['description'] = description + config['Messages']['eventor_import']
                if 'base_url' in feed and feed['base_url'] != '':
                    url = feed['base_url'] + '/' + calendar_name + '?calendarEventId=' + idrottonline_id
                    component['url'] = url
                    component['description'] = component['description'] + ' ' + config['Messages'][
                        'original_ref'] + ' ' + url

                calendar.add_component(component)
    except IOError as e:
        logging.info(e)
        return


def generate_calendarfeed(days_in_advance: int, config, bucket_name):
    logging.info('Trying to create calendar feed')
    calendar = Calendar()
    calendar['method'] = 'REQUEST'
    calendar['prodid'] = '-//Svenska Orienteringsförbundet//' + config['General']['name']
    calendar['version'] = '2.0'

    start = date.today()
    end = start + timedelta(days=days_in_advance)

    # Fetch club activities
    activities_root = eventor_utils.club_activities(start, end, config)
    add_activities(activities_root, calendar, config)

    # Fetch district events
    if config['Calendar']['district_event_class_ids'].rstrip() != '':
        districts_events_root = eventor_utils.events(start, end, config['Calendar']['district_event_class_ids'].split(','),
                                                    [config['EventorApi']['district_id']], config)
        add_events(districts_events_root, calendar, config)

    # Fetch club events
    if config['Calendar']['club_event_class_ids'].rstrip() != '':
        club_events_root = eventor_utils.events(start, end, config['Calendar']['club_event_class_ids'].split(','),
                                                [config['EventorApi']['organisation_id']], config)
        add_events(club_events_root, calendar, config)

    # Add feeds from other webcals
    add_idrottonline_feeds(calendar)

    # f = open(ROOT_DIR + '/' + config['Calendar']['filename'], 'wb')
    # f.write(calendar.to_ical())
    # f.close()
    # logging.info('Calendar feed created')

    google_utils.upload_blob(bucket_name, calendar.to_ical(), 'latest_calendar.ics')

    #return jsonify({'message': 'Calendarfeed successfully generated for next {} days'.format(days_in_advance)})
    print('Calendarfeed successfully generated for next {} days'.format(days_in_advance))

def overwrite_changed(calendar):
    if config['Calendar']['target_feed'].rstrip() == '':
        return
    target_feed = Calendar.from_ical(api_request('GET', config['Calendar']['target_feed'], '', '').text)

    target_dict = dict()
    for component in target_feed.subcomponents:
        if 'UID' in component and 'DESCRIPTION' in component:
            target_dict[component['UID']] = component['DESCRIPTION']

    for component in calendar.subcomponents:
        if 'UID' in component and component['UID'] in target_dict:
            component['DESCRIPTION'] = target_dict[component['UID']]


def fetch_calendarfeed():
    if not os.path.exists(config['Calendar']['filename']):
        logging.warning(f'Calendarfeed file {config["Calendar"]["filename"]} not generated')
        return jsonify({"message": "Calendarfeed not generated"}), 503

    latest_ics = ROOT_DIR + '/' + config['Calendar']['filename']
    with open(latest_ics, 'rb') as f:
        calendar = Calendar.from_ical(f.read())

    try:
        response = make_response(calendar.to_ical())
        response.headers["Content-Disposition"] = "attachment; filename=Events.ics"
        return response
    except IOError as e:
        logging.error(e)
        raise KnownError(config['Messages']['io_error'], 'eventor')


@calendarfeeds_app.route('/calendarfeed', methods=['GET'])
@calendarfeeds_app.route('/calendarfeed/<int:days_in_advance>', methods=['POST'])
def calendarfeed(days_in_advance: int = None):
    if request.method == 'POST':
        logging.info(f'Calendar POST request from {request.remote_addr}')
        if not check_api_key(request.headers):
            logging.warning('Wrong API key')
            return jsonify({"message": "ERROR: Unauthorized"}), 401
        if isinstance(days_in_advance, int):
            return generate_calendarfeed(days_in_advance)
        else:
            logging.warning('Days in advance misspecified')
            return jsonify('Specify how many days to generate feed for'), 400
    elif request.method == 'GET':
        logging.info(f'Calendar GET request from {request.remote_addr}')
        return fetch_calendarfeed()
