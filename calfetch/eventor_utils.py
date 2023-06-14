import json
import xml.etree.cElementTree as ET
from datetime import date
import logging

from cache_to_disk import cache_to_disk

from calfetch.request_handler import api_request
from common import KnownError
from definitions import config, ROOT_DIR

#organisation_id = config.getint('EventorApi', 'organisation_id')


def eventor_request(method, api_endpoint, config, query_params: dict = None, headers: dict = None, success_codes=(200,)):
    return api_request(method, api_endpoint, config['Messages']['eventor_fail'], 'eventor', query_params, headers,
                       success_codes)


def club_activities(start_date: date, end_date: date, config):
    query_params = {'from': start_date.strftime('%Y-%m-%d'),
                    'to': end_date.strftime('%Y-%m-%d'),
                    'organisationId': config['EventorApi']['organisation_id']}

    headers = {'ApiKey': config['EventorApi']['apikey']}
    xml_str = eventor_request('GET', config['EventorApi']['activities_endpoint'], config, query_params, headers).text
    logging.info(f'Fetched club activities between {start_date} and {end_date}')
    return ET.fromstring(xml_str)


def events(start_date: date, end_date: date, classification_ids: list, organisations_ids: list, config):
    query_params = {'fromDate': start_date.strftime('%Y-%m-%d'),
                    'toDate': end_date.strftime('%Y-%m-%d'),
                    'classificationIds': ','.join(map(str, classification_ids)),
                    'organisationIds': ','.join(map(str, organisations_ids))
                    }

    headers = {'ApiKey': config['EventorApi']['apikey']}
    xml_str = eventor_request('GET', config['EventorApi']['events_endpoint'], config, query_params, headers).text
    logging.info(f'Fetched events between {start_date} and {end_date}')
    return ET.fromstring(xml_str)


@cache_to_disk(100)
def org_name(org_id: int):
    headers = {'ApiKey': config['EventorApi']['apikey']}
    xml_str = eventor_request('GET', config['EventorApi']['organisation_endpoint'] + '/' + org_id, headers=headers).text
    root = ET.fromstring(xml_str)
    return root.find('Name').text


def extract_info(columns_dict: dict, person: ET.Element):
    person_info_dict = {column: '' for column in columns_dict.keys()}

    for column_name, column_dict in columns_dict.items():
        person_info_dict[column_name] = find_value(column_dict['path'], person)
        if 'length' in column_dict.keys():
            person_info_dict[column_name] = person_info_dict[column_name][:int(column_dict['length'])]

    return person_info_dict


def person_in_organisation(person_info, org_id: int):
    roles = person_info.findall('Role')
    for r in roles:
        role_org = r.find('OrganisationId')
        if role_org is not None and int(role_org.text) == org_id:
            return True
    return False


def fetch_members():
    api_endpoint = config['EventorApi']['members_endpoint'] + '/' + config['EventorApi']['organisation_id']
    query_params = {'includeContactDetails': 'true'}
    headers = {'ApiKey': config['EventorApi']['apikey']}
    xml_str = eventor_request('GET', api_endpoint, query_params=query_params, headers=headers).text
    logging.info('Fetched member records from Eventor')

    return ET.fromstring(xml_str)


def get_membership(person_info):
    organisation_id = person_info.find('OrganisationId')
    if organisation_id is not None and organisation_id.text != config['EventorApi']['organisation_id']:
        return config['Wordpress']['guest_member']
    return config['Wordpress']['member']


def find_value(path: list, person: ET.Element):
    element = person
    element_path = path[0]
    for child in element_path:
        element = element.find(child)
        if element is None:
            return ''

    if len(path) == 1:
        return element.text

    values = [value for key, value in element.attrib.items() if key in path[1]]

    return ', '.join(values)


def validate_eventor_user(eventor_user, eventor_password):
    headers = {'Username': eventor_user, 'Password': eventor_password}
    logging.info(f'Trying validate Eventor user {eventor_user}')
    request = eventor_request('GET', config['EventorApi']['authenticate_endpoint'],
                              headers=headers, success_codes=(200, 403))
    if request.status_code == 403:
        logging.warning(f'Failed to validate Eventor user {eventor_user}. Full error: {request.text}')
        raise KnownError(config['Messages']['eventor_validation_fail'], 'eventor')
    logging.info(f'Fetched person info for eventor user {eventor_user}')

    person_info = ET.fromstring(request.text)
    # Check if Eventor user is member of organization
    if not person_in_organisation(person_info, organisation_id):
        logging.warning(f'Eventor user {eventor_user} not found in organization')
        raise KnownError(config['Messages']['not_in_club'], 'eventor')

    # Create dict with essential person info
    eventor_info_dict = dict()
    eventor_info_dict['first_name'] = find_value([["PersonName", "Given"]], person_info)
    eventor_info_dict['last_name'] = find_value([["PersonName", "Family"]], person_info)
    eventor_info_dict['id'] = find_value([["PersonId"]], person_info)
    eventor_info_dict['membership'] = get_membership(person_info)

    logging.info(f'User with eventor id {eventor_user} validated as {eventor_info_dict["membership"]}')

    return eventor_info_dict


def get_members_matrix():
    parse_settings_file = ROOT_DIR + '/' + config['Member']['parse_settings_file']
    with open(parse_settings_file, encoding='utf-8') as f:
        columns_dict = json.load(f)

    # Fetch XML with current members
    root = fetch_members()

    array = [list(columns_dict.keys())]

    for i, person in enumerate(root):
        person_info = extract_info(columns_dict, person)
        array.append(list(person_info.values()))

    return array
