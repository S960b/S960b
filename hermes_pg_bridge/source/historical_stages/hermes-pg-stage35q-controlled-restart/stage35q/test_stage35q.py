import importlib.util, pathlib, unittest, re
p=pathlib.Path(__file__).with_name('stage35q.py')
s=importlib.util.spec_from_file_location('s',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Tests(unittest.TestCase):
    def test_key_validation(self):
        self.assertIsNotNone(re.fullmatch(rb'[A-Za-z0-9_-]{64}',b'A'*64))
        self.assertIsNone(re.fullmatch(rb'[A-Za-z0-9_-]{64}',b'A'*64+b'\n'))
    def test_fixed_units(self):
        self.assertTrue(m.NEW.endswith('stage35jk.service'))
        self.assertTrue(m.OLD.endswith('stage33fd.service'))
    def test_stop(self):
        with self.assertRaises(m.Stop):m.require(False,'TEST')
    def test_link_check(self):
        self.assertFalse(m.regular_owned(pathlib.Path('/does/not/exist')))
    def test_single_use_marker(self):
        self.assertTrue(str(m.MARK).startswith('/home/kali/.config/bridge-pg-v2/'))
    def test_trace_patterns(self):
        self.assertTrue(re.search(r'(?<!\d)403(?!\d)','HTTP 403'))
        self.assertFalse(re.search(r'(?<!\d)403(?!\d)','4030'))
if __name__=='__main__':unittest.main()
