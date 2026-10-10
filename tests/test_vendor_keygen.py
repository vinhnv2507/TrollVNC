import base64
import hashlib
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from PySide6.QtCore import QSettings, QDateTime
from PySide6.QtWidgets import QApplication
from vendor_keygen.core import KeygenError, issue_keys, load_manager, load_signer, parse_devices, read_udid
from vendor_keygen.ui import KeygenWindow

UDID='a'*40
app=QApplication.instance() or QApplication([])


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.signer=ec.generate_private_key(ec.SECP256R1())

    def test_exact_payload_and_signature_multiple_devices(self):
        results=issue_keys(self.signer,'6s1 | '+UDID+'\n'+'12345678-1234567890abcdef','existing-manager-token',1100,now=1000)
        self.assertEqual(len(results),2)
        for result in results:
            left,right=result.license.split('.')
            payload=base64.urlsafe_b64decode(left+'='*(-len(left)%4))
            signature=base64.urlsafe_b64decode(right+'='*(-len(right)%4))
            self.signer.public_key().verify(signature,payload,ec.ECDSA(hashes.SHA256()))
            self.assertEqual(json.loads(payload),{'v':1,'udid':result.udid,'exp':1100,'tok':'existing-manager-token'})

    def test_invalid_inputs_and_expiry_boundary_and_forever(self):
        for text in ['',UDID+'\n'+UDID.upper(),'SERIALNUMBER','name | bad','../bad']:
            with self.assertRaises(KeygenError):parse_devices(text)
        for token in ['','bad token','bad\ncommand','é'*10]:
            with self.assertRaises(KeygenError):issue_keys(self.signer,UDID,token,1100,1000)
        for expiry in [-1,1000,999,True,1.1]:
            with self.assertRaises(KeygenError):issue_keys(self.signer,UDID,'token',expiry,1000)
        self.assertEqual(issue_keys(self.signer,UDID,'token',0,1000)[0].expiry,0)

    def test_private_key_reading_password_curve_and_pinned_public_key(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'private.pem'
            public=self.signer.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
            expected=hashlib.sha256(public).hexdigest()
            path.write_bytes(self.signer.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.BestAvailableEncryption(b'secret')))
            self.assertEqual(load_signer(path,'secret',expected).private_numbers(),self.signer.private_numbers())
            for password, fingerprint in [('',expected),('wrong',expected),('secret','wrong')]:
                with self.assertRaises(KeygenError):load_signer(path,password,fingerprint)
            bad=ec.generate_private_key(ec.SECP384R1())
            path.write_bytes(bad.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            with self.assertRaises(KeygenError):load_signer(path,expected=expected)

    def test_manager_config_validates_token_and_inherits_control_ports(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'devices.json'
            path.write_text(json.dumps({'settings':{'control_token':'secret','control_port':1234},'devices':[{'host':'1'},{'host':'2','control_port':5678}]}),encoding='utf-8')
            token,devices=load_manager(path)
            self.assertEqual(token,'secret');self.assertEqual([d['control_port'] for d in devices],[1234,5678])
            path.write_text('{}',encoding='utf-8')
            with self.assertRaises(KeygenError):load_manager(path)

    def test_authenticated_lan_udid_is_available_after_expiry(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1',0));server.listen()
            received=[]
            def handle():
                connection,_=server.accept()
                with connection:
                    received.append(connection.recv(1024))
                    connection.sendall(('OK invalid remaining=0 udid='+UDID+'\n').encode())
            thread=threading.Thread(target=handle);thread.start()
            self.assertEqual(read_udid('127.0.0.1',server.getsockname()[1],'token'),UDID)
            thread.join(2);self.assertFalse(thread.is_alive())
            self.assertEqual(received,[b'auth token license\n'])


class KeygenUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.settings=QSettings(str(Path(self.temp.name)/'settings.ini'),QSettings.IniFormat)
        self.window=KeygenWindow(self.settings,autodetect=False)
        self.window.token.setText('existing-token');self.window.devices.setPlainText('6s1 | '+UDID)
        self.signer=ec.generate_private_key(ec.SECP256R1())

    def tearDown(self):
        self.window.close();self.window.deleteLater();app.processEvents()

    def test_generate_copy_export_and_clear_without_persisting_credentials(self):
        with patch('vendor_keygen.ui.load_signer',return_value=self.signer):self.window.generate()
        self.assertEqual(self.window.table.rowCount(),1)
        self.window.copy_selected();self.assertEqual(app.clipboard().text(),self.window.results[0].license)
        self.window.copy_all();self.assertIn(UDID+'\t',app.clipboard().text())
        export=Path(self.temp.name)/'keys.txt'
        with patch('vendor_keygen.ui.QFileDialog.getSaveFileName',return_value=(str(export),'')):self.window.export()
        self.assertIn(self.window.results[0].license,export.read_text(encoding='utf-8'))
        self.assertNotIn('existing-token',str(self.settings.allKeys()))
        self.window.clear_results();self.assertEqual(self.window.table.rowCount(),0);self.assertFalse(self.window.copy_button.isEnabled())

    def test_bad_input_does_not_replace_existing_results(self):
        with patch('vendor_keygen.ui.load_signer',return_value=self.signer):
            self.window.generate();original=self.window.results
            self.window.devices.setPlainText('SERIAL');self.window.generate()
            self.assertIs(self.window.results,original);self.assertIn('UDID',self.window.status.text())

    def test_duration_modes_and_exact_fixed_date(self):
        self.window.mode.setCurrentIndex(3);self.window.days.setValue(12)
        self.assertAlmostEqual(self.window.expiry()-time.time(),12*86400,delta=2)
        self.window.mode.setCurrentIndex(4);dt=QDateTime.currentDateTime().addDays(5);self.window.date.setDateTime(dt)
        self.assertEqual(self.window.expiry(),dt.toSecsSinceEpoch())
        self.window.mode.setCurrentIndex(5);self.assertEqual(self.window.expiry(),0)

    def test_lan_result_appends_name_once_and_config_selects_custom_port(self):
        path=Path(self.temp.name)/'devices.json'
        path.write_text(json.dumps({'settings':{'control_token':'token','control_port':1234},'devices':[{'host':'127.0.0.1','name':'6s1'}]}),encoding='utf-8')
        self.window._load_config(path);self.window.device_combo.setCurrentIndex(1)
        self.assertEqual(self.window.port.value(),1234)
        self.window.devices.clear();self.window._udid_result(UDID,'');self.window._udid_result(UDID,'')
        self.assertEqual(self.window.devices.toPlainText(),'6s1 | '+UDID)


if __name__=='__main__':unittest.main()
