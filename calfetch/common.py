from definitions import config


class KnownError(Exception):
    def __init__(self, message, error_type=None):
        self.message = message
        self.error_type = error_type

    def __str__(self):
        return self.message


def check_api_key(headers):
    if config['ApiSettings']['ApiKey'].rstrip() == '':
        return True
    auth = headers.get("X-Api-Key")
    return auth == config['ApiSettings']['ApiKey']
