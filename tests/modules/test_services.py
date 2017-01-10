import json
import os
import zipfile
import shutil

from os.path import join
from bzt import NormalShutdown, ToolError, TaurusConfigError
from bzt.engine import Service, Provisioning, EngineModule
from bzt.modules.blazemeter import CloudProvisioning, BlazeMeterClientEmul
from bzt.modules.services import Unpacker, InstallChecker, AppiumLoader
from bzt.utils import get_files_recursive, get_full_path, is_windows
from tests import BZTestCase, __dir__
from tests.mocks import EngineEmul, ModuleMock
from bzt.modules.selenium import Node


class TestZipFolder(BZTestCase):
    def test_pack_and_send_to_blazemeter(self):
        obj = CloudProvisioning()
        obj.engine = EngineEmul()

        obj.engine.config.merge({
            "execution": {
                "executor": "selenium",
                "concurrency": 5500,
                "locations": {
                    "us-east-1": 1,
                    "us-west": 2},
                "scenario": {
                    "script": __dir__() + "/../selenium/java_package"}},
            "modules": {
                "selenium": "bzt.modules.selenium.SeleniumExecutor",
                "cloud": "bzt.modules.blazemeter.CloudProvisioning"},
            "provisioning": "cloud"
        })

        obj.parameters = obj.engine.config['execution']
        obj.settings["token"] = "FakeToken"
        obj.client = client = BlazeMeterClientEmul(obj.log)
        client.results.append({"result": []})  # collections
        client.results.append({"result": []})  # tests
        client.results.append(self.__get_user_info())  # user
        client.results.append({"result": {"id": id(client)}})  # create test
        client.results.append({"files": []})  # create test
        client.results.append({})  # upload files
        client.results.append({"result": {"id": id(obj)}})  # start
        client.results.append({"result": {"id": id(obj)}})  # get master
        client.results.append({"result": []})  # get master sessions
        client.results.append({})  # terminate

        obj.prepare()

        unpack_cfgs = obj.engine.config.get(Service.SERV)
        self.assertEqual(len(unpack_cfgs), 1)
        self.assertEqual(unpack_cfgs[0]['module'], Unpacker.UNPACK)
        self.assertEqual(unpack_cfgs[0][Unpacker.FILES], ['java_package.zip'])
        self.assertTrue(zipfile.is_zipfile(obj.engine.artifacts_dir + '/java_package.zip'))

    @staticmethod
    def __get_user_info():
        with open(__dir__() + "/../json/blazemeter-api-user.json") as fhd:
            return json.loads(fhd.read())

    def test_receive_and_unpack_on_worker(self):
        obj = Unpacker()
        obj.engine = EngineEmul()
        obj.engine.config.merge({
            "execution": {
                "executor": "selenium",
                "concurrency": 5500,
                "scenario": {
                    "script": "java_package.zip"}},
            "modules": {
                "selenium": "bzt.modules.selenium.SeleniumExecutor",
                "cloud": "bzt.modules.blazemeter.CloudProvisioning"},
            "provisioning": "local"
        })
        obj.engine.file_search_paths = [obj.engine.artifacts_dir]

        obj.parameters["files"] = ["java_package.zip"]

        # create archive and put it in artifact dir
        source = __dir__() + "/../selenium/java_package"
        zip_name = obj.engine.create_artifact('java_package', '.zip')
        with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_STORED) as zip_file:
            for filename in get_files_recursive(source):
                zip_file.write(filename, filename[len(os.path.dirname(source)):])

        obj.prepare()

        # check unpacked tree
        destination = obj.engine.artifacts_dir + '/java_package'
        result_tree = set(filename[len(destination):] for filename in get_files_recursive(destination))
        original_tree = set(filename[len(source):] for filename in get_files_recursive(source))
        self.assertEqual(result_tree, original_tree)

    def test_no_work_prov(self):
        obj = Service()
        obj.engine = EngineEmul()
        obj.engine.config[Provisioning.PROV] = 'cloud'
        self.assertFalse(obj.should_run())
        obj.parameters['run-at'] = 'cloud'
        self.assertTrue(obj.should_run())


class TestToolInstaller(BZTestCase):
    def test_regular(self):
        obj = InstallChecker()
        obj.engine = EngineEmul()
        obj.engine.config.get("modules")["base"] = EngineModule.__module__ + "." + EngineModule.__name__
        obj.engine.config.get("modules")["dummy"] = ModuleMock.__module__ + "." + ModuleMock.__name__
        self.assertRaises(NormalShutdown, obj.prepare)

    def test_problematic(self):
        obj = InstallChecker()
        obj.engine = EngineEmul()
        obj.engine.config.get("modules")["err"] = "hello there"
        self.assertRaises(ToolError, obj.prepare)


class TestAppiumLoaderCheckInstall(BZTestCase):
    def setUp(self):
        self.engine = EngineEmul()
        self.engine.config.merge({'services': {'appium-loader': {}}})
        self.appium = AppiumLoader()
        self.appium.engine = self.engine
        self.appium.settings = self.engine.config['services']['appium-loader']
        self.check_if_node_installed = Node.check_if_installed()
        Node.check_if_installed = lambda slf: True

    def tearDown(self):
        Node.check_if_installed = self.check_if_node_installed

    def test_no_sdk(self):
        os.environ['ANDROID_HOME'] = ''
        self.assertRaises(TaurusConfigError, self.appium.prepare)

    def test_sdk_from_conf(self):
        os.environ['ANDROID_HOME'] = ''
        self.appium.settings['sdk-path'] = 'from_config'
        self.assertRaises(ToolError, self.appium.prepare)
        self.assertIn('from_config', self.appium.sdk_path)

    def test_sdk_from_env(self):
        path_to_andhome = join(self.engine.artifacts_dir, 'from_env')
        path_to_tools = join(path_to_andhome, 'tools')
        os.environ['ANDROID_HOME'] = path_to_andhome
        self.appium.settings['sdk-path'] = None
        self.cp_utils(path_to_tools)

        self.appium.prepare()
        self.assertEqual(path_to_andhome, self.appium.sdk_path)
        self.assertRaises(TaurusConfigError, self.appium.startup)
        self.appium.shutdown()
        self.appium.post_process()

    def test_two_way(self):
        path_to_andhome = join(self.engine.artifacts_dir, 'from_config')
        path_to_tools = join(path_to_andhome, 'tools')
        os.environ['ANDROID_HOME'] = 'from_env'
        self.appium.settings['sdk-path'] = path_to_andhome
        self.cp_utils(path_to_tools)
        self.appium.settings['avd'] = 'my_little_android'

        self.appium.prepare()
        self.assertEqual(path_to_andhome, self.appium.sdk_path)
        self.appium.startup()
        self.appium.shutdown()
        self.appium.post_process()

    def cp_utils(self, tools_dir):
        os.mkdir(get_full_path(tools_dir, step_up=1))
        os.mkdir(tools_dir)

        if is_windows():
            suffix = '.bat'
        else:
            suffix = ''
        ap_dir = join(__dir__(), '..', 'appium')

        shutil.copy2(join(ap_dir, 'appium' + suffix), self.engine.artifacts_dir)
        os.chmod(join(self.engine.artifacts_dir, 'appium' + suffix), 0o755)
        shutil.copy2(join(ap_dir, 'appium.py'), self.engine.artifacts_dir)
        os.environ['PATH'] = self.engine.artifacts_dir + os.pathsep + os.environ['PATH']

        shutil.copy2(join(ap_dir, 'android' + suffix), tools_dir)
        os.chmod(join(tools_dir, 'android' + suffix), 0o755)
        shutil.copy2(join(ap_dir, 'emulator' + suffix), tools_dir)
        os.chmod(join(tools_dir, 'emulator' + suffix), 0o755)
        shutil.copy2(join(ap_dir, 'emulator.py'), join(tools_dir, 'emulator.py'))


class MockWebDriverRemote(object):
    def __init__(self, addr, caps):
        self.addr = addr
        self.caps = caps
        self.cmd_list = []
        self.data = []

    def get(self):
        self.cmd_list.append('get')
        return self.data.pop()

    def page_source(self):
        self.cmd_list.append('page_source')
        return self.data.pop()

    def quit(self):
        self.cmd_list.append('quit')

