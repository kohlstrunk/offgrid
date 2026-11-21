import unittest
from sensoren import parse_mo_w
from sensor_cache import SensorCache

MOTOR_ON = '10 min 2500 rpm Tk 69.8 Tp 43.2 750 W 100 % h: 7905 18%'
MOTOR_OFF = '0 min 0 rpm Tk 69.8 Tp 43.2 0 W 0 % h: 7905 0%'

class MotorZustaende(unittest.TestCase):
    def test_praefix_und_altformat(self):
        self.assertEqual(parse_mo_w('AAN ' + MOTOR_ON).zustandskennung, 'AAN')
        self.assertEqual(parse_mo_w(MOTOR_ON).zustand, 'LAEUFT')
        self.assertEqual(parse_mo_w('AMAUS ' + MOTOR_OFF).zustand, 'REINIGUNG')
        self.assertEqual(parse_mo_w('AAUS ' + MOTOR_OFF).zustand, 'AUS_BEREIT')
        self.assertEqual(parse_mo_w(MOTOR_OFF).zustand, 'AUS_STATUS_UNBEKANNT')
        self.assertEqual(parse_mo_w('AAN ' + MOTOR_OFF).zustand, 'WIDERSPRUCH')
    def test_bereitschaft_und_reinigung(self):
        c = SensorCache()
        c.aufnehmen(MOTOR_OFF)
        self.assertFalse(c.motorstatus()['startbereit'])
        self.assertIsNone(c.aufnehmen('AMAUS'))
        self.assertFalse(c.motorstatus()['startbereit'])
        c.aufnehmen('AMAUS ' + MOTOR_OFF)
        self.assertEqual(c.motorstatus()['zustand'], 'REINIGUNG')
        c.aufnehmen('AAUS ' + MOTOR_OFF)
        self.assertTrue(c.motorstatus()['startbereit'])
        c.aufnehmen('AAN ' + MOTOR_ON)
        self.assertFalse(c.motorstatus()['startbereit'])
        c.aufnehmen(MOTOR_OFF)
        self.assertFalse(c.motorstatus()['startbereit'])
    def test_keine_mo_s_abfrage(self):
        from sensor_cache import PARSER
        from dienst import Dienst
        self.assertNotIn('MO_S', PARSER)
        self.assertNotIn('MO_S', Dienst().letzte_anfrage)

    def test_grenze_70(self):
        c = SensorCache()
        c.aufnehmen('AAUS ' + MOTOR_OFF.replace('Tk 69.8','Tk 70.0'))
        self.assertEqual(c.motorstatus()['zustand'], 'AUS_ZU_HEISS')
        self.assertFalse(c.motorstatus()['startbereit'])
    def test_keine_statuswort_aus_fremdtext(self):
        c = SensorCache()
        self.assertIsNone(c.aufnehmen('Hinweis: AAUS erwartet'))
        self.assertIsNone(c.aufnehmen('MO_S'))
