import configparser
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG = ROOT_DIR + '/config.cfg'

config = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation(), )
config.read(CONFIG, encoding='utf8')
