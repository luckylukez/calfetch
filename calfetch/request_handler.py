import logging
import requests
from requests import HTTPError

from common import KnownError
from definitions import config


def api_request(method, api_endpoint, error_message, error_category, query_params=None, headers=None,
                success_codes=(200,)):
    try:
        if method == 'GET':
            r = requests.get(url=api_endpoint, params=query_params, headers=headers)
        elif method == 'POST':
            r = requests.post(url=api_endpoint, params=query_params, headers=headers)
        else:
            logging.error('Not implemented API request method')
            raise Exception(config['Messages']['request_bug'])
        if r.status_code not in success_codes:
            logging.error(
                f'{method} request to {api_endpoint} failed. Status code: {r.status_code}, reason: {r.reason}, text: {r.text}')
            raise KnownError(error_message, error_category)
        else:
            logging.info(f'{method} request to {api_endpoint} successful. Status code: {r.status_code}')
    except HTTPError:
        raise KnownError(error_message, error_category)

    r.encoding = 'utf-8'
    return r
