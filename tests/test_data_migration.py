import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from controlios.config import DeviceSpec, Settings, Registry
from controlios.data_migration import MARKER, migrate_user_data


class DataMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old, self.new = self.root/'old', self.root/'new'

    def put(self, root, name, value):
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def migrate(self, sources=None):
        return migrate_user_data(self.new, sources or [self.old], asdict(Settings()), asdict(DeviceSpec('')))

    def test_recovers_auto_discovered_devices_settings_and_all_stores(self):
        old = Registry(devices=[DeviceSpec('a',name='6s1',group='g1',note='note'),DeviceSpec('b')],
                       settings=Settings(control_token='old-token',live_fps=12))
        old.save(self.old/'config/devices.json')
        Registry(devices=[DeviceSpec('a')]).save(self.new/'config/devices.json')
        before=(self.new/'config/devices.json').read_bytes()
        self.put(self.old,'config/shopee_accounts.json',{'version':1,'accounts':[{'id':'x','proxy':'proxy'}]})
        self.put(self.old,'config/autoclick_js.json',{'Live':'tap(.5,.5)'})
        self.put(self.old,'cookies/a/shopee.json',{'cookie':'private-cookie'})
        for name in ['config/earnapp_monitor.py','config/id_controlios','captures/photo.png']:
            path=self.old/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'keep-original')
        report=self.migrate()
        loaded=Registry.load(self.new/'config/devices.json')
        self.assertEqual(loaded,old)
        self.assertEqual(report['errors'],0)
        for name in ['config/shopee_accounts.json','config/autoclick_js.json','cookies/a/shopee.json',
                     'config/earnapp_monitor.py','config/id_controlios','captures/photo.png']:
            self.assertEqual((self.old/name).read_bytes(),(self.new/name).read_bytes())
        self.assertEqual(next((self.new/'migration-backups/config').glob('devices.json.*.bak')).read_bytes(),before)
        self.assertEqual(Registry.load(self.old/'config/devices.json'),old)

    def test_keeps_manager_edits_and_new_devices_and_script_conflicts(self):
        Registry(devices=[DeviceSpec('a',name='old',group='old-group',note='old-note')],
                 settings=Settings(control_token='old-token')).save(self.old/'config/devices.json')
        Registry(devices=[DeviceSpec('a',name='new',group='new-group',note='new-note'),DeviceSpec('c')],
                 settings=Settings(control_token='new-token')).save(self.new/'config/devices.json')
        self.put(self.old,'config/scripts.json',{'old':'old-script','same':'old'})
        self.put(self.new,'config/scripts.json',{'same':'new','new':'new-script'})
        self.migrate()
        loaded=Registry.load(self.new/'config/devices.json')
        self.assertEqual([d.host for d in loaded.devices],['a','c'])
        self.assertEqual(loaded.devices[0].note,'new-note')
        self.assertEqual(loaded.devices[0].group,'new-group')
        self.assertEqual(loaded.settings.control_token,'new-token')
        self.assertEqual(json.loads((self.new/'config/scripts.json').read_text()),
                         {'old':'old-script','same':'new','new':'new-script'})

    def test_import_runs_once_and_does_not_restore_deleted_devices(self):
        Registry(devices=[DeviceSpec('a')]).save(self.old/'config/devices.json')
        self.migrate()
        Registry().save(self.new/'config/devices.json')
        self.assertTrue(self.migrate()['already_imported'])
        self.assertEqual(Registry.load(self.new/'config/devices.json').devices,[])

    def test_appdata_source_wins_over_stale_portable_and_duplicate_sources(self):
        portable=self.root/'portable'
        Registry(devices=[DeviceSpec('a',name='current')]).save(self.old/'config/devices.json')
        Registry(devices=[DeviceSpec('b')]).save(portable/'config/devices.json')
        self.migrate([self.old,portable,self.old,self.new])
        self.assertEqual([d.host for d in Registry.load(self.new/'config/devices.json').devices],['a'])

    def test_empty_shopee_table_recovers_but_existing_table_is_preserved(self):
        self.put(self.old,'config/shopee_accounts.json',{'accounts':[{'id':'old'}]})
        self.put(self.new,'config/shopee_accounts.json',{'accounts':[]})
        self.migrate()
        self.assertEqual(json.loads((self.new/'config/shopee_accounts.json').read_text())['accounts'],[{'id':'old'}])
        (self.new/'config'/MARKER).unlink()
        self.put(self.new,'config/shopee_accounts.json',{'accounts':[{'id':'new'}]})
        self.migrate()
        self.assertEqual(json.loads((self.new/'config/shopee_accounts.json').read_text())['accounts'],[{'id':'new'}])

    def test_corrupt_json_and_write_failures_are_retryable(self):
        self.put(self.old,'config/scripts.json',{'ok':'body'})
        (self.old/'config/scripts.json').write_text('{broken',encoding='utf-8')
        self.assertGreater(self.migrate()['errors'],0)
        self.assertFalse((self.new/'config'/MARKER).exists())
        self.put(self.old,'config/scripts.json',{'ok':'body'})
        with patch('controlios.data_migration._write',side_effect=PermissionError()):
            self.assertGreater(self.migrate()['errors'],0)
        self.assertEqual(self.migrate()['errors'],0)
        self.assertTrue((self.new/'config'/MARKER).exists())

    def test_missing_sources_do_not_mark_complete_and_cache_is_skipped(self):
        self.migrate()
        self.assertFalse((self.new/'config'/MARKER).exists())
        self.put(self.old,'config/scripts.json',{})
        self.put(self.old,'captures/_media_tmp/cache.json',{})
        self.migrate()
        self.assertFalse((self.new/'captures/_media_tmp').exists())


if __name__=='__main__':unittest.main()
