# Copyright (c) 2014 Alex Meade.  All rights reserved.
# Copyright (c) 2014 Clinton Knight.  All rights reserved.
# Copyright (c) 2014 Andrew Kerr.  All rights reserved.
# Copyright (c) 2015 Tom Barron.  All rights reserved.
# Copyright (c) 2015 Goutham Pacha Ravi. All rights reserved.
# Copyright (c) 2015 Dustin Schoenbrun. All rights reserved.
# Copyright (c) 2016 Chuck Fouts. All rights reserved.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
Mock unit tests for the NetApp block storage library
"""

import copy
import uuid

import ddt
import mock
from oslo_log import versionutils
from oslo_utils import units
import six

from cinder import exception
from cinder.i18n import _, _LW
from cinder import test
from cinder.tests.unit.volume.drivers.netapp.dataontap import fakes as fake
import cinder.tests.unit.volume.drivers.netapp.fakes as na_fakes
from cinder.volume.drivers.netapp.dataontap import block_base
from cinder.volume.drivers.netapp.dataontap.client import api as netapp_api
from cinder.volume.drivers.netapp import utils as na_utils
from cinder.volume import utils as volume_utils


@ddt.ddt
class NetAppBlockStorageLibraryTestCase(test.TestCase):

    def setUp(self):
        super(NetAppBlockStorageLibraryTestCase, self).setUp()

        kwargs = {'configuration': self.get_config_base()}
        self.library = block_base.NetAppBlockStorageLibrary(
            'driver', 'protocol', **kwargs)
        self.library.zapi_client = mock.Mock()
        self.zapi_client = self.library.zapi_client
        self.mock_request = mock.Mock()

    def tearDown(self):
        super(NetAppBlockStorageLibraryTestCase, self).tearDown()

    def get_config_base(self):
        return na_fakes.create_configuration()

    @mock.patch.object(versionutils, 'report_deprecated_feature')
    def test_get_reserved_percentage_default_multipler(self, mock_report):

        default = 1.2
        reserved_percentage = 20.0
        self.library.configuration.netapp_size_multiplier = default
        self.library.configuration.reserved_percentage = reserved_percentage

        result = self.library._get_reserved_percentage()

        self.assertEqual(reserved_percentage, result)
        self.assertFalse(mock_report.called)

    @mock.patch.object(versionutils, 'report_deprecated_feature')
    def test_get_reserved_percentage(self, mock_report):

        multiplier = 2.0
        self.library.configuration.netapp_size_multiplier = multiplier

        result = self.library._get_reserved_percentage()

        reserved_ratio = round(1 - (1 / multiplier), 2)
        reserved_percentage = 100 * int(reserved_ratio)

        self.assertEqual(reserved_percentage, result)
        msg = _LW('The "netapp_size_multiplier" configuration option is '
                  'deprecated and will be removed in the Mitaka release. '
                  'Please set "reserved_percentage = %d" instead.') % (
                      result)
        mock_report.assert_called_once_with(block_base.LOG, msg)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_lun_attr',
                       mock.Mock(return_value={'Volume': 'FAKE_CMODE_VOL1'}))
    def test_get_pool(self):
        pool = self.library.get_pool({'name': 'volume-fake-uuid'})
        self.assertEqual('FAKE_CMODE_VOL1', pool)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_lun_attr',
                       mock.Mock(return_value=None))
    def test_get_pool_no_metadata(self):
        pool = self.library.get_pool({'name': 'volume-fake-uuid'})
        self.assertIsNone(pool)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_lun_attr',
                       mock.Mock(return_value=dict()))
    def test_get_pool_volume_unknown(self):
        pool = self.library.get_pool({'name': 'volume-fake-uuid'})
        self.assertIsNone(pool)

    def test_create_volume(self):
        volume_size_in_bytes = int(fake.SIZE) * units.Gi
        self.mock_object(na_utils, 'get_volume_extra_specs')
        self.mock_object(na_utils, 'log_extra_spec_warnings')
        self.mock_object(block_base, 'LOG')
        self.mock_object(volume_utils, 'extract_host', mock.Mock(
            return_value=fake.POOL_NAME))
        self.mock_object(self.library, '_setup_qos_for_volume',
                         mock.Mock(return_value=None))
        self.mock_object(self.library, '_create_lun')
        self.mock_object(self.library, '_create_lun_handle')
        self.mock_object(self.library, '_add_lun_to_table')
        self.mock_object(self.library, '_mark_qos_policy_group_for_deletion')

        self.library.create_volume(fake.VOLUME)

        self.library._create_lun.assert_called_once_with(
            fake.POOL_NAME, fake.LUN_NAME, volume_size_in_bytes,
            fake.LUN_METADATA, None)
        self.assertEqual(0, self.library.
                         _mark_qos_policy_group_for_deletion.call_count)
        self.assertEqual(0, block_base.LOG.error.call_count)

    def test_create_volume_no_pool(self):
        self.mock_object(volume_utils, 'extract_host', mock.Mock(
            return_value=None))

        self.assertRaises(exception.InvalidHost, self.library.create_volume,
                          fake.VOLUME)

    def test_create_volume_exception_path(self):
        self.mock_object(block_base, 'LOG')
        self.mock_object(na_utils, 'get_volume_extra_specs')
        self.mock_object(self.library, '_setup_qos_for_volume',
                         mock.Mock(return_value=None))
        self.mock_object(self.library, '_create_lun', mock.Mock(
            side_effect=Exception))
        self.mock_object(self.library, '_mark_qos_policy_group_for_deletion')

        self.assertRaises(exception.VolumeBackendAPIException,
                          self.library.create_volume, fake.VOLUME)

        self.assertEqual(1, self.library.
                         _mark_qos_policy_group_for_deletion.call_count)
        self.assertEqual(1, block_base.LOG.exception.call_count)

    def test_create_volume_no_pool_provided_by_scheduler(self):
        fake_volume = copy.deepcopy(fake.VOLUME)
        # Set up fake volume whose 'host' field is missing pool information.
        fake_volume['host'] = '%s@%s' % (fake.HOST_NAME, fake.BACKEND_NAME)

        self.assertRaises(exception.InvalidHost, self.library.create_volume,
                          fake_volume)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_lun_attr')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_or_create_igroup')
    def test_map_lun(self, mock_get_or_create_igroup, mock_get_lun_attr):
        os = 'linux'
        protocol = 'fcp'
        self.library.host_type = 'linux'
        mock_get_lun_attr.return_value = {'Path': fake.LUN_PATH, 'OsType': os}
        mock_get_or_create_igroup.return_value = (fake.IGROUP1_NAME, os,
                                                  'iscsi')
        self.zapi_client.map_lun.return_value = '1'

        lun_id = self.library._map_lun('fake_volume',
                                       fake.FC_FORMATTED_INITIATORS,
                                       protocol, None)

        self.assertEqual('1', lun_id)
        mock_get_or_create_igroup.assert_called_once_with(
            fake.FC_FORMATTED_INITIATORS, protocol, os)
        self.zapi_client.map_lun.assert_called_once_with(
            fake.LUN_PATH, fake.IGROUP1_NAME, lun_id=None)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary, '_get_lun_attr')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_or_create_igroup')
    @mock.patch.object(block_base, 'LOG', mock.Mock())
    def test_map_lun_mismatch_host_os(
            self, mock_get_or_create_igroup, mock_get_lun_attr):
        os = 'windows'
        protocol = 'fcp'
        self.library.host_type = 'linux'
        mock_get_lun_attr.return_value = {'Path': fake.LUN_PATH, 'OsType': os}
        mock_get_or_create_igroup.return_value = (fake.IGROUP1_NAME, os,
                                                  'iscsi')
        self.library._map_lun('fake_volume',
                              fake.FC_FORMATTED_INITIATORS,
                              protocol, None)
        mock_get_or_create_igroup.assert_called_once_with(
            fake.FC_FORMATTED_INITIATORS, protocol,
            self.library.host_type)
        self.zapi_client.map_lun.assert_called_once_with(
            fake.LUN_PATH, fake.IGROUP1_NAME, lun_id=None)
        self.assertEqual(1, block_base.LOG.warning.call_count)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_lun_attr')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_or_create_igroup')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_find_mapped_lun_igroup')
    def test_map_lun_preexisting(self, mock_find_mapped_lun_igroup,
                                 mock_get_or_create_igroup, mock_get_lun_attr):
        os = 'linux'
        protocol = 'fcp'
        mock_get_lun_attr.return_value = {'Path': fake.LUN_PATH, 'OsType': os}
        mock_get_or_create_igroup.return_value = (fake.IGROUP1_NAME, os,
                                                  'iscsi')
        mock_find_mapped_lun_igroup.return_value = (fake.IGROUP1_NAME, '2')
        self.zapi_client.map_lun.side_effect = netapp_api.NaApiError

        lun_id = self.library._map_lun(
            'fake_volume', fake.FC_FORMATTED_INITIATORS, protocol, None)

        self.assertEqual('2', lun_id)
        mock_find_mapped_lun_igroup.assert_called_once_with(
            fake.LUN_PATH, fake.FC_FORMATTED_INITIATORS)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_lun_attr')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_or_create_igroup')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_find_mapped_lun_igroup')
    def test_map_lun_api_error(self, mock_find_mapped_lun_igroup,
                               mock_get_or_create_igroup, mock_get_lun_attr):
        os = 'linux'
        protocol = 'fcp'
        mock_get_lun_attr.return_value = {'Path': fake.LUN_PATH, 'OsType': os}
        mock_get_or_create_igroup.return_value = (fake.IGROUP1_NAME, os,
                                                  'iscsi')
        mock_find_mapped_lun_igroup.return_value = (None, None)
        self.zapi_client.map_lun.side_effect = netapp_api.NaApiError

        self.assertRaises(netapp_api.NaApiError, self.library._map_lun,
                          'fake_volume', fake.FC_FORMATTED_INITIATORS,
                          protocol, None)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_find_mapped_lun_igroup')
    def test_unmap_lun(self, mock_find_mapped_lun_igroup):
        mock_find_mapped_lun_igroup.return_value = (fake.IGROUP1_NAME, 1)

        self.library._unmap_lun(fake.LUN_PATH, fake.FC_FORMATTED_INITIATORS)

        self.zapi_client.unmap_lun.assert_called_once_with(fake.LUN_PATH,
                                                           fake.IGROUP1_NAME)

    def test_find_mapped_lun_igroup(self):
        self.assertRaises(NotImplementedError,
                          self.library._find_mapped_lun_igroup,
                          fake.LUN_PATH,
                          fake.FC_FORMATTED_INITIATORS)

    def test_has_luns_mapped_to_initiators(self):
        self.zapi_client.has_luns_mapped_to_initiators.return_value = True
        self.assertTrue(self.library._has_luns_mapped_to_initiators(
            fake.FC_FORMATTED_INITIATORS))
        self.zapi_client.has_luns_mapped_to_initiators.assert_called_once_with(
            fake.FC_FORMATTED_INITIATORS)

    def test_get_or_create_igroup_preexisting(self):
        self.zapi_client.get_igroup_by_initiators.return_value = [fake.IGROUP1]
        self.library._create_igroup_add_initiators = mock.Mock()
        igroup_name, host_os, ig_type = self.library._get_or_create_igroup(
            fake.FC_FORMATTED_INITIATORS, 'fcp', 'linux')

        self.assertEqual(fake.IGROUP1_NAME, igroup_name)
        self.assertEqual('linux', host_os)
        self.assertEqual('fcp', ig_type)
        self.zapi_client.get_igroup_by_initiators.assert_called_once_with(
            fake.FC_FORMATTED_INITIATORS)
        self.assertEqual(
            0, self.library._create_igroup_add_initiators.call_count)

    @mock.patch.object(uuid, 'uuid4', mock.Mock(return_value=fake.UUID1))
    def test_get_or_create_igroup_none_preexisting(self):
        """This method also tests _create_igroup_add_initiators."""
        self.zapi_client.get_igroup_by_initiators.return_value = []

        igroup_name, os, ig_type = self.library._get_or_create_igroup(
            fake.FC_FORMATTED_INITIATORS, 'fcp', 'linux')

        self.assertEqual('openstack-' + fake.UUID1, igroup_name)
        self.zapi_client.create_igroup.assert_called_once_with(
            igroup_name, 'fcp', 'linux')
        self.assertEqual(len(fake.FC_FORMATTED_INITIATORS),
                         self.zapi_client.add_igroup_initiator.call_count)
        self.assertEqual('linux', os)
        self.assertEqual('fcp', ig_type)

    def test_get_fc_target_wwpns(self):
        self.assertRaises(NotImplementedError,
                          self.library._get_fc_target_wwpns)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_build_initiator_target_map')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_map_lun')
    def test_initialize_connection_fc(self, mock_map_lun,
                                      mock_build_initiator_target_map):
        self.maxDiff = None
        mock_map_lun.return_value = '1'
        mock_build_initiator_target_map.return_value = (fake.FC_TARGET_WWPNS,
                                                        fake.FC_I_T_MAP, 4)

        target_info = self.library.initialize_connection_fc(fake.FC_VOLUME,
                                                            fake.FC_CONNECTOR)

        self.assertDictEqual(target_info, fake.FC_TARGET_INFO)
        mock_map_lun.assert_called_once_with(
            'fake_volume', fake.FC_FORMATTED_INITIATORS, 'fcp', None)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_build_initiator_target_map')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_map_lun')
    def test_initialize_connection_fc_no_wwpns(
            self, mock_map_lun, mock_build_initiator_target_map):

        mock_map_lun.return_value = '1'
        mock_build_initiator_target_map.return_value = (None, None, 0)
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.library.initialize_connection_fc,
                          fake.FC_VOLUME,
                          fake.FC_CONNECTOR)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_has_luns_mapped_to_initiators')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_unmap_lun')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_lun_attr')
    def test_terminate_connection_fc(self, mock_get_lun_attr, mock_unmap_lun,
                                     mock_has_luns_mapped_to_initiators):

        mock_get_lun_attr.return_value = {'Path': fake.LUN_PATH}
        mock_unmap_lun.return_value = None
        mock_has_luns_mapped_to_initiators.return_value = True

        target_info = self.library.terminate_connection_fc(fake.FC_VOLUME,
                                                           fake.FC_CONNECTOR)

        self.assertDictEqual(target_info, fake.FC_TARGET_INFO_EMPTY)
        mock_unmap_lun.assert_called_once_with(fake.LUN_PATH,
                                               fake.FC_FORMATTED_INITIATORS)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_build_initiator_target_map')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_has_luns_mapped_to_initiators')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_unmap_lun')
    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_lun_attr')
    def test_terminate_connection_fc_no_more_luns(
            self, mock_get_lun_attr, mock_unmap_lun,
            mock_has_luns_mapped_to_initiators,
            mock_build_initiator_target_map):

        mock_get_lun_attr.return_value = {'Path': fake.LUN_PATH}
        mock_unmap_lun.return_value = None
        mock_has_luns_mapped_to_initiators.return_value = False
        mock_build_initiator_target_map.return_value = (fake.FC_TARGET_WWPNS,
                                                        fake.FC_I_T_MAP, 4)

        target_info = self.library.terminate_connection_fc(fake.FC_VOLUME,
                                                           fake.FC_CONNECTOR)

        self.assertDictEqual(target_info, fake.FC_TARGET_INFO_UNMAP)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_fc_target_wwpns')
    def test_build_initiator_target_map_no_lookup_service(
            self, mock_get_fc_target_wwpns):

        self.library.lookup_service = None
        mock_get_fc_target_wwpns.return_value = fake.FC_FORMATTED_TARGET_WWPNS

        (target_wwpns, init_targ_map, num_paths) = \
            self.library._build_initiator_target_map(fake.FC_CONNECTOR)

        self.assertSetEqual(set(fake.FC_TARGET_WWPNS), set(target_wwpns))
        self.assertDictEqual(fake.FC_I_T_MAP_COMPLETE, init_targ_map)
        self.assertEqual(0, num_paths)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_fc_target_wwpns')
    def test_build_initiator_target_map_with_lookup_service(
            self, mock_get_fc_target_wwpns):

        self.library.lookup_service = mock.Mock()
        self.library.lookup_service.get_device_mapping_from_network.\
            return_value = fake.FC_FABRIC_MAP
        mock_get_fc_target_wwpns.return_value = fake.FC_FORMATTED_TARGET_WWPNS

        (target_wwpns, init_targ_map, num_paths) = \
            self.library._build_initiator_target_map(fake.FC_CONNECTOR)

        self.assertSetEqual(set(fake.FC_TARGET_WWPNS), set(target_wwpns))
        self.assertDictEqual(fake.FC_I_T_MAP, init_targ_map)
        self.assertEqual(4, num_paths)

    @mock.patch.object(na_utils, 'check_flags')
    def test_do_setup_san_configured(self, mock_check_flags):
        self.library.configuration.netapp_lun_ostype = 'windows'
        self.library.configuration.netapp_host_type = 'solaris'
        self.library.configuration.netapp_lun_space_reservation = 'disabled'
        self.library.do_setup(mock.Mock())
        self.assertTrue(mock_check_flags.called)
        self.assertEqual('windows', self.library.lun_ostype)
        self.assertEqual('solaris', self.library.host_type)

    @mock.patch.object(na_utils, 'check_flags')
    def test_do_setup_san_unconfigured(self, mock_check_flags):
        self.library.configuration.netapp_lun_ostype = None
        self.library.configuration.netapp_host_type = None
        self.library.configuration.netapp_lun_space_reservation = 'enabled'
        self.library.do_setup(mock.Mock())
        self.assertTrue(mock_check_flags.called)
        self.assertEqual('linux', self.library.lun_ostype)
        self.assertEqual('linux', self.library.host_type)

    def test_do_setup_space_reservation_disabled(self):
        self.mock_object(na_utils, 'check_flags')
        self.library.configuration.netapp_lun_ostype = None
        self.library.configuration.netapp_host_type = None
        self.library.configuration.netapp_lun_space_reservation = 'disabled'

        self.library.do_setup(mock.Mock())

        self.assertEqual('false', self.library.lun_space_reservation)

    def test_do_setup_space_reservation_enabled(self):
        self.mock_object(na_utils, 'check_flags')
        self.library.configuration.netapp_lun_ostype = None
        self.library.configuration.netapp_host_type = None
        self.library.configuration.netapp_lun_space_reservation = 'enabled'

        self.library.do_setup(mock.Mock())

        self.assertEqual('true', self.library.lun_space_reservation)

    def test_get_existing_vol_with_manage_ref_no_source_info(self):

        self.assertRaises(exception.ManageExistingInvalidReference,
                          self.library._get_existing_vol_with_manage_ref,
                          {})

    def test_get_existing_vol_manage_not_found(self):

        self.zapi_client.get_lun_by_args.return_value = []

        self.assertRaises(exception.ManageExistingInvalidReference,
                          self.library._get_existing_vol_with_manage_ref,
                          {'source-name': 'lun_path'})
        self.assertEqual(1, self.zapi_client.get_lun_by_args.call_count)

    def test_get_existing_vol_manage_lun_by_path(self):

        self.library.vserver = 'fake_vserver'
        self.zapi_client.get_lun_by_args.return_value = ['lun0', 'lun1']
        mock_lun = block_base.NetAppLun(
            'lun0', 'lun0', '3', {'UUID': 'fake_uuid'})
        self.mock_object(block_base.NetAppBlockStorageLibrary,
                         '_extract_lun_info',
                         mock.Mock(return_value=mock_lun))

        existing_ref = {'source-name': 'fake_path'}
        lun = self.library._get_existing_vol_with_manage_ref(existing_ref)

        self.zapi_client.get_lun_by_args.assert_called_once_with(
            path='fake_path')
        self.library._extract_lun_info.assert_called_once_with('lun0')
        self.assertEqual('lun0', lun.name)

    def test_get_existing_vol_manage_lun_by_uuid(self):

        self.library.vserver = 'fake_vserver'
        self.zapi_client.get_lun_by_args.return_value = ['lun0', 'lun1']
        mock_lun = block_base.NetAppLun(
            'lun0', 'lun0', '3', {'UUID': 'fake_uuid'})
        self.mock_object(block_base.NetAppBlockStorageLibrary,
                         '_extract_lun_info',
                         mock.Mock(return_value=mock_lun))

        existing_ref = {'source-id': 'fake_uuid'}
        lun = self.library._get_existing_vol_with_manage_ref(existing_ref)

        self.zapi_client.get_lun_by_args.assert_called_once_with(
            uuid='fake_uuid')
        self.library._extract_lun_info.assert_called_once_with('lun0')
        self.assertEqual('lun0', lun.name)

    def test_get_existing_vol_manage_lun_invalid_mode(self):

        self.assertRaises(exception.ManageExistingInvalidReference,
                          self.library._get_existing_vol_with_manage_ref,
                          {'source-id': 'src_id'})

    def test_get_existing_vol_manage_lun_invalid_lun(self):

        self.zapi_client.get_lun_by_args.return_value = ['lun0', 'lun1']
        self.mock_object(block_base.NetAppBlockStorageLibrary,
                         '_is_lun_valid_on_storage',
                         mock.Mock(side_effect=[False, True]))
        mock_lun0 = block_base.NetAppLun(
            'lun0', 'lun0', '3', {'UUID': 'src_id_0'})
        mock_lun1 = block_base.NetAppLun(
            'lun1', 'lun1', '5', {'UUID': 'src_id_1'})
        self.mock_object(block_base.NetAppBlockStorageLibrary,
                         '_extract_lun_info',
                         mock.Mock(side_effect=[mock_lun0, mock_lun1]))

        lun = self.library._get_existing_vol_with_manage_ref(
            {'source-name': 'lun_path'})

        self.assertEqual(1, self.zapi_client.get_lun_by_args.call_count)
        self.library._extract_lun_info.assert_has_calls([
            mock.call('lun0'),
            mock.call('lun1'),
        ])
        self.assertEqual('lun1', lun.name)

    @mock.patch.object(block_base.NetAppBlockStorageLibrary,
                       '_get_existing_vol_with_manage_ref',
                       mock.Mock(return_value=block_base.NetAppLun(
                                 'handle', 'name', '1073742824', {})))
    def test_manage_existing_get_size(self):
        size = self.library.manage_existing_get_size(
            {'id': 'vol_id'}, {'ref': 'ref'})
        self.assertEqual(2, size)
        self.library._get_existing_vol_with_manage_ref.assert_called_once_with(
            {'ref': 'ref'})

    @mock.patch.object(block_base.LOG, 'info')
    def test_unmanage(self, log):
        mock_lun = block_base.NetAppLun('handle', 'name', '1',
                                        {'Path': 'p', 'UUID': 'uuid'})
        self.library._get_lun_from_table = mock.Mock(return_value=mock_lun)
        self.library.unmanage({'name': 'vol'})
        self.library._get_lun_from_table.assert_called_once_with('vol')
        self.assertEqual(1, log.call_count)

    def test_check_vol_type_for_lun(self):
        self.assertRaises(NotImplementedError,
                          self.library._check_volume_type_for_lun,
                          'vol', 'lun', 'existing_ref', {})

    def test_is_lun_valid_on_storage(self):
        self.assertTrue(self.library._is_lun_valid_on_storage('lun'))

    def test_initialize_connection_iscsi(self):
        target_details_list = fake.ISCSI_TARGET_DETAILS_LIST
        volume = fake.ISCSI_VOLUME
        connector = fake.ISCSI_CONNECTOR
        self.mock_object(block_base.NetAppBlockStorageLibrary, '_map_lun',
                         mock.Mock(return_value=fake.ISCSI_LUN['lun_id']))
        self.zapi_client.get_iscsi_target_details.return_value = (
            target_details_list)
        self.mock_object(block_base.NetAppBlockStorageLibrary,
                         '_get_preferred_target_from_list',
                         mock.Mock(return_value=target_details_list[1]))
        self.zapi_client.get_iscsi_service_details.return_value = (
            fake.ISCSI_SERVICE_IQN)
        self.mock_object(
            na_utils, 'get_iscsi_connection_properties',
            mock.Mock(return_value=fake.ISCSI_CONNECTION_PROPERTIES))

        target_info = self.library.initialize_connection_iscsi(volume,
                                                               connector)

        self.assertEqual(
            fake.ISCSI_CONNECTION_PROPERTIES['data']['auth_method'],
            target_info['data']['auth_method'])
        self.assertEqual(
            fake.ISCSI_CONNECTION_PROPERTIES['data']['auth_password'],
            target_info['data']['auth_password'])
        self.assertTrue('auth_password' in target_info['data'])

        self.assertEqual(
            fake.ISCSI_CONNECTION_PROPERTIES['data']['discovery_auth_method'],
            target_info['data']['discovery_auth_method'])
        self.assertEqual(
            fake.ISCSI_CONNECTION_PROPERTIES['data']
            ['discovery_auth_password'],
            target_info['data']['discovery_auth_password'])
        self.assertTrue('auth_password' in target_info['data'])
        self.assertEqual(
            fake.ISCSI_CONNECTION_PROPERTIES['data']
            ['discovery_auth_username'],
            target_info['data']['discovery_auth_username'])

        self.assertEqual(fake.ISCSI_CONNECTION_PROPERTIES, target_info)
        block_base.NetAppBlockStorageLibrary._map_lun.assert_called_once_with(
            fake.ISCSI_VOLUME['name'], [fake.ISCSI_CONNECTOR['initiator']],
            'iscsi', None)
        self.zapi_client.get_iscsi_target_details.assert_called_once_with()
        block_base.NetAppBlockStorageLibrary._get_preferred_target_from_list\
                                            .assert_called_once_with(
                                                target_details_list)
        self.zapi_client.get_iscsi_service_details.assert_called_once_with()

    def test_initialize_connection_iscsi_no_target_list(self):
        volume = fake.ISCSI_VOLUME
        connector = fake.ISCSI_CONNECTOR
        self.mock_object(block_base.NetAppBlockStorageLibrary, '_map_lun',
                         mock.Mock(return_value=fake.ISCSI_LUN['lun_id']))
        self.zapi_client.get_iscsi_target_details.return_value = None
        self.mock_object(block_base.NetAppBlockStorageLibrary,
                         '_get_preferred_target_from_list')
        self.mock_object(
            na_utils, 'get_iscsi_connection_properties',
            mock.Mock(return_value=fake.ISCSI_CONNECTION_PROPERTIES))

        self.assertRaises(exception.VolumeBackendAPIException,
                          self.library.initialize_connection_iscsi,
                          volume, connector)

        self.assertEqual(
            0, block_base.NetAppBlockStorageLibrary
                         ._get_preferred_target_from_list.call_count)
        self.assertEqual(
            0, self.zapi_client.get_iscsi_service_details.call_count)
        self.assertEqual(
            0, na_utils.get_iscsi_connection_properties.call_count)

    def test_initialize_connection_iscsi_no_preferred_target(self):
        volume = fake.ISCSI_VOLUME
        connector = fake.ISCSI_CONNECTOR
        self.mock_object(block_base.NetAppBlockStorageLibrary, '_map_lun',
                         mock.Mock(return_value=fake.ISCSI_LUN['lun_id']))
        self.zapi_client.get_iscsi_target_details.return_value = None
        self.mock_object(block_base.NetAppBlockStorageLibrary,
                         '_get_preferred_target_from_list',
                         mock.Mock(return_value=None))
        self.mock_object(na_utils, 'get_iscsi_connection_properties')

        self.assertRaises(exception.VolumeBackendAPIException,
                          self.library.initialize_connection_iscsi,
                          volume, connector)

        self.assertEqual(0, self.zapi_client
                                .get_iscsi_service_details.call_count)
        self.assertEqual(0, na_utils.get_iscsi_connection_properties
                                    .call_count)

    def test_initialize_connection_iscsi_no_iscsi_service_details(self):
        target_details_list = fake.ISCSI_TARGET_DETAILS_LIST
        volume = fake.ISCSI_VOLUME
        connector = fake.ISCSI_CONNECTOR
        self.mock_object(block_base.NetAppBlockStorageLibrary, '_map_lun',
                         mock.Mock(return_value=fake.ISCSI_LUN['lun_id']))
        self.zapi_client.get_iscsi_target_details.return_value = (
            target_details_list)
        self.mock_object(block_base.NetAppBlockStorageLibrary,
                         '_get_preferred_target_from_list',
                         mock.Mock(return_value=target_details_list[1]))
        self.zapi_client.get_iscsi_service_details.return_value = None
        self.mock_object(na_utils, 'get_iscsi_connection_properties')

        self.assertRaises(exception.VolumeBackendAPIException,
                          self.library.initialize_connection_iscsi,
                          volume,
                          connector)

        block_base.NetAppBlockStorageLibrary._map_lun.assert_called_once_with(
            fake.ISCSI_VOLUME['name'], [fake.ISCSI_CONNECTOR['initiator']],
            'iscsi', None)
        self.zapi_client.get_iscsi_target_details.assert_called_once_with()
        block_base.NetAppBlockStorageLibrary._get_preferred_target_from_list\
                  .assert_called_once_with(target_details_list)

    def test_get_target_details_list(self):
        target_details_list = fake.ISCSI_TARGET_DETAILS_LIST

        result = self.library._get_preferred_target_from_list(
            target_details_list)

        self.assertEqual(target_details_list[0], result)

    def test_get_preferred_target_from_empty_list(self):
        target_details_list = []

        result = self.library._get_preferred_target_from_list(
            target_details_list)

        self.assertIsNone(result)

    def test_get_preferred_target_from_list_with_one_interface_disabled(self):
        target_details_list = copy.deepcopy(fake.ISCSI_TARGET_DETAILS_LIST)
        target_details_list[0]['interface-enabled'] = 'false'

        result = self.library._get_preferred_target_from_list(
            target_details_list)

        self.assertEqual(target_details_list[1], result)

    def test_get_preferred_target_from_list_with_all_interfaces_disabled(self):
        target_details_list = copy.deepcopy(fake.ISCSI_TARGET_DETAILS_LIST)
        for target in target_details_list:
            target['interface-enabled'] = 'false'

        result = self.library._get_preferred_target_from_list(
            target_details_list)

        self.assertEqual(target_details_list[0], result)

    def test_get_preferred_target_from_list_with_filter(self):
        target_details_list = fake.ISCSI_TARGET_DETAILS_LIST
        filter = [target_detail['address']
                  for target_detail in target_details_list[1:]]

        result = self.library._get_preferred_target_from_list(
            target_details_list, filter)

        self.assertEqual(target_details_list[1], result)

    @mock.patch.object(na_utils, 'check_flags', mock.Mock())
    @mock.patch.object(block_base, 'LOG', mock.Mock())
    def test_setup_error_invalid_lun_os(self):
        self.library.configuration.netapp_lun_ostype = 'unknown'
        self.library.do_setup(mock.Mock())
        self.assertRaises(exception.NetAppDriverException,
                          self.library.check_for_setup_error)
        msg = _("Invalid value for NetApp configuration"
                " option netapp_lun_ostype.")
        block_base.LOG.error.assert_called_once_with(msg)

    @mock.patch.object(na_utils, 'check_flags', mock.Mock())
    @mock.patch.object(block_base, 'LOG', mock.Mock())
    def test_setup_error_invalid_host_type(self):
        self.library.configuration.netapp_lun_ostype = 'linux'
        self.library.configuration.netapp_host_type = 'future_os'
        self.library.do_setup(mock.Mock())

        self.assertRaises(exception.NetAppDriverException,
                          self.library.check_for_setup_error)

        msg = _("Invalid value for NetApp configuration"
                " option netapp_host_type.")
        block_base.LOG.error.assert_called_once_with(msg)

    @mock.patch.object(na_utils, 'check_flags', mock.Mock())
    def test_check_for_setup_error_both_config(self):
        self.library.configuration.netapp_lun_ostype = 'linux'
        self.library.configuration.netapp_host_type = 'linux'
        self.library.do_setup(mock.Mock())
        self.zapi_client.get_lun_list.return_value = ['lun1']
        self.library._extract_and_populate_luns = mock.Mock()
        self.library.check_for_setup_error()
        self.library._extract_and_populate_luns.assert_called_once_with(
            ['lun1'])

    @mock.patch.object(na_utils, 'check_flags', mock.Mock())
    def test_check_for_setup_error_no_os_host(self):
        self.library.configuration.netapp_lun_ostype = None
        self.library.configuration.netapp_host_type = None
        self.library.do_setup(mock.Mock())
        self.zapi_client.get_lun_list.return_value = ['lun1']
        self.library._extract_and_populate_luns = mock.Mock()
        self.library.check_for_setup_error()
        self.library._extract_and_populate_luns.assert_called_once_with(
            ['lun1'])

    def test_delete_volume(self):
        mock_delete_lun = self.mock_object(self.library, '_delete_lun')

        self.library.delete_volume(fake.VOLUME)

        mock_delete_lun.assert_called_once_with(fake.LUN_NAME)

    def test_delete_lun(self):
        mock_get_lun_attr = self.mock_object(self.library, '_get_lun_attr')
        mock_get_lun_attr.return_value = fake.LUN_METADATA
        self.library.zapi_client = mock.Mock()
        self.library.lun_table = fake.LUN_TABLE

        self.library._delete_lun(fake.LUN_NAME)

        mock_get_lun_attr.assert_called_once_with(
            fake.LUN_NAME, 'metadata')
        self.library.zapi_client.destroy_lun.assert_called_once_with(fake.PATH)

    def test_delete_lun_no_metadata(self):
        self.mock_object(self.library, '_get_lun_attr', mock.Mock(
            return_value=None))
        self.library.zapi_client = mock.Mock()
        self.mock_object(self.library, 'zapi_client')

        self.library._delete_lun(fake.LUN_NAME)

        self.library._get_lun_attr.assert_called_once_with(
            fake.LUN_NAME, 'metadata')
        self.assertEqual(0, self.library.zapi_client.destroy_lun.call_count)
        self.assertEqual(0,
                         self.zapi_client.
                         mark_qos_policy_group_for_deletion.call_count)

    def test_delete_snapshot(self):
        mock_delete_lun = self.mock_object(self.library, '_delete_lun')

        self.library.delete_snapshot(fake.SNAPSHOT)

        mock_delete_lun.assert_called_once_with(fake.SNAPSHOT_NAME)

    def test_clone_source_to_destination(self):
        self.mock_object(na_utils, 'get_volume_extra_specs', mock.Mock(
            return_value=fake.EXTRA_SPECS))
        self.mock_object(self.library, '_setup_qos_for_volume', mock.Mock(
            return_value=fake.QOS_POLICY_GROUP_INFO))
        self.mock_object(self.library, '_clone_lun')
        self.mock_object(self.library, '_extend_volume')
        self.mock_object(self.library, 'delete_volume')
        self.mock_object(self.library, '_mark_qos_policy_group_for_deletion')
        self.library.lun_space_reservation = 'false'

        self.library._clone_source_to_destination(fake.CLONE_SOURCE,
                                                  fake.CLONE_DESTINATION)

        na_utils.get_volume_extra_specs.assert_called_once_with(
            fake.CLONE_DESTINATION)
        self.library._setup_qos_for_volume.assert_called_once_with(
            fake.CLONE_DESTINATION, fake.EXTRA_SPECS)
        self.library._clone_lun.assert_called_once_with(
            fake.CLONE_SOURCE_NAME, fake.CLONE_DESTINATION_NAME,
            space_reserved='false',
            qos_policy_group_name=fake.QOS_POLICY_GROUP_NAME)
        self.library._extend_volume.assert_called_once_with(
            fake.CLONE_DESTINATION, fake.CLONE_DESTINATION_SIZE,
            fake.QOS_POLICY_GROUP_NAME)
        self.assertEqual(0, self.library.delete_volume.call_count)
        self.assertEqual(0, self.library.
                         _mark_qos_policy_group_for_deletion.call_count)

    def test_clone_source_to_destination_exception_path(self):
        self.mock_object(na_utils, 'get_volume_extra_specs', mock.Mock(
            return_value=fake.EXTRA_SPECS))
        self.mock_object(self.library, '_setup_qos_for_volume', mock.Mock(
            return_value=fake.QOS_POLICY_GROUP_INFO))
        self.mock_object(self.library, '_clone_lun')
        self.mock_object(self.library, '_extend_volume', mock.Mock(
            side_effect=Exception))
        self.mock_object(self.library, 'delete_volume')
        self.mock_object(self.library, '_mark_qos_policy_group_for_deletion')
        self.library.lun_space_reservation = 'true'

        self.assertRaises(exception.VolumeBackendAPIException,
                          self.library._clone_source_to_destination,
                          fake.CLONE_SOURCE, fake.CLONE_DESTINATION)

        na_utils.get_volume_extra_specs.assert_called_once_with(
            fake.CLONE_DESTINATION)
        self.library._setup_qos_for_volume.assert_called_once_with(
            fake.CLONE_DESTINATION, fake.EXTRA_SPECS)
        self.library._clone_lun.assert_called_once_with(
            fake.CLONE_SOURCE_NAME, fake.CLONE_DESTINATION_NAME,
            space_reserved='true',
            qos_policy_group_name=fake.QOS_POLICY_GROUP_NAME)
        self.library._extend_volume.assert_called_once_with(
            fake.CLONE_DESTINATION, fake.CLONE_DESTINATION_SIZE,
            fake.QOS_POLICY_GROUP_NAME)
        self.assertEqual(1, self.library.delete_volume.call_count)
        self.assertEqual(1, self.library.
                         _mark_qos_policy_group_for_deletion.call_count)

    def test_create_lun(self):
        self.assertRaises(NotImplementedError, self.library._create_lun,
                          fake.VOLUME_ID, fake.LUN_ID, fake.SIZE,
                          fake.LUN_METADATA)

    def test_clone_lun(self):
        self.assertRaises(NotImplementedError, self.library._clone_lun,
                          fake.VOLUME_ID, 'new-' + fake.VOLUME_ID)

    def test_create_volume_from_snapshot(self):
        mock_do_clone = self.mock_object(self.library,
                                         '_clone_source_to_destination')
        source = {
            'name': fake.SNAPSHOT['name'],
            'size': fake.SNAPSHOT['volume_size']
        }

        self.library.create_volume_from_snapshot(fake.VOLUME, fake.SNAPSHOT)

        mock_do_clone.assert_has_calls([
            mock.call(source, fake.VOLUME)])

    def test_create_cloned_volume(self):
        fake_lun = block_base.NetAppLun(fake.LUN_HANDLE, fake.LUN_ID,
                                        fake.LUN_SIZE, fake.LUN_METADATA)
        mock_get_lun_from_table = self.mock_object(self.library,
                                                   '_get_lun_from_table')
        mock_get_lun_from_table.return_value = fake_lun
        mock_do_clone = self.mock_object(self.library,
                                         '_clone_source_to_destination')
        source = {
            'name': fake_lun.name,
            'size': fake.VOLUME_REF['size']
        }

        self.library.create_cloned_volume(fake.VOLUME, fake.VOLUME_REF)

        mock_do_clone.assert_has_calls([
            mock.call(source, fake.VOLUME)])

    def test_extend_volume(self):

        new_size = 100
        volume_copy = copy.copy(fake.VOLUME)
        volume_copy['size'] = new_size

        mock_get_volume_extra_specs = self.mock_object(
            na_utils, 'get_volume_extra_specs',
            mock.Mock(return_value=fake.EXTRA_SPECS))
        mock_setup_qos_for_volume = self.mock_object(
            self.library, '_setup_qos_for_volume',
            mock.Mock(return_value=fake.QOS_POLICY_GROUP_INFO))
        mock_extend_volume = self.mock_object(self.library, '_extend_volume')

        self.library.extend_volume(fake.VOLUME, new_size)

        mock_get_volume_extra_specs.assert_called_once_with(fake.VOLUME)
        mock_setup_qos_for_volume.assert_called_once_with(volume_copy,
                                                          fake.EXTRA_SPECS)
        mock_extend_volume.assert_called_once_with(fake.VOLUME,
                                                   new_size,
                                                   fake.QOS_POLICY_GROUP_NAME)

    def test_extend_volume_api_error(self):

        new_size = 100
        volume_copy = copy.copy(fake.VOLUME)
        volume_copy['size'] = new_size

        mock_get_volume_extra_specs = self.mock_object(
            na_utils, 'get_volume_extra_specs',
            mock.Mock(return_value=fake.EXTRA_SPECS))
        mock_setup_qos_for_volume = self.mock_object(
            self.library, '_setup_qos_for_volume',
            mock.Mock(return_value=fake.QOS_POLICY_GROUP_INFO))
        mock_extend_volume = self.mock_object(
            self.library, '_extend_volume',
            mock.Mock(side_effect=netapp_api.NaApiError))

        self.assertRaises(netapp_api.NaApiError,
                          self.library.extend_volume,
                          fake.VOLUME,
                          new_size)

        mock_get_volume_extra_specs.assert_called_once_with(fake.VOLUME)
        mock_setup_qos_for_volume.assert_has_calls([
            mock.call(volume_copy, fake.EXTRA_SPECS),
            mock.call(fake.VOLUME, fake.EXTRA_SPECS)])
        mock_extend_volume.assert_called_once_with(
            fake.VOLUME, new_size, fake.QOS_POLICY_GROUP_NAME)

    def test__extend_volume_direct(self):

        current_size = fake.LUN_SIZE
        current_size_bytes = current_size * units.Gi
        new_size = fake.LUN_SIZE * 2
        new_size_bytes = new_size * units.Gi
        max_size = fake.LUN_SIZE * 10
        max_size_bytes = max_size * units.Gi
        fake_volume = copy.copy(fake.VOLUME)
        fake_volume['size'] = new_size

        fake_lun = block_base.NetAppLun(fake.LUN_HANDLE,
                                        fake.LUN_ID,
                                        current_size_bytes,
                                        fake.LUN_METADATA)
        mock_get_lun_from_table = self.mock_object(
            self.library, '_get_lun_from_table',
            mock.Mock(return_value=fake_lun))
        fake_lun_geometry = {'max_resize': six.text_type(max_size_bytes)}
        mock_get_lun_geometry = self.mock_object(
            self.library.zapi_client, 'get_lun_geometry',
            mock.Mock(return_value=fake_lun_geometry))
        mock_do_direct_resize = self.mock_object(self.library.zapi_client,
                                                 'do_direct_resize')
        mock_do_sub_clone_resize = self.mock_object(self.library,
                                                    '_do_sub_clone_resize')
        self.library.lun_table = {fake.VOLUME['name']: fake_lun}

        self.library._extend_volume(fake.VOLUME, new_size, 'fake_qos_policy')

        mock_get_lun_from_table.assert_called_once_with(fake.VOLUME['name'])
        mock_get_lun_geometry.assert_called_once_with(
            fake.LUN_METADATA['Path'])
        mock_do_direct_resize.assert_called_once_with(
            fake.LUN_METADATA['Path'], six.text_type(new_size_bytes))
        self.assertFalse(mock_do_sub_clone_resize.called)
        self.assertEqual(six.text_type(new_size_bytes),
                         self.library.lun_table[fake.VOLUME['name']].size)

    def test__extend_volume_clone(self):

        current_size = fake.LUN_SIZE
        current_size_bytes = current_size * units.Gi
        new_size = fake.LUN_SIZE * 20
        new_size_bytes = new_size * units.Gi
        max_size = fake.LUN_SIZE * 10
        max_size_bytes = max_size * units.Gi
        fake_volume = copy.copy(fake.VOLUME)
        fake_volume['size'] = new_size

        fake_lun = block_base.NetAppLun(fake.LUN_HANDLE,
                                        fake.LUN_ID,
                                        current_size_bytes,
                                        fake.LUN_METADATA)
        mock_get_lun_from_table = self.mock_object(
            self.library, '_get_lun_from_table',
            mock.Mock(return_value=fake_lun))
        fake_lun_geometry = {'max_resize': six.text_type(max_size_bytes)}
        mock_get_lun_geometry = self.mock_object(
            self.library.zapi_client, 'get_lun_geometry',
            mock.Mock(return_value=fake_lun_geometry))
        mock_do_direct_resize = self.mock_object(self.library.zapi_client,
                                                 'do_direct_resize')
        mock_do_sub_clone_resize = self.mock_object(self.library,
                                                    '_do_sub_clone_resize')
        self.library.lun_table = {fake.VOLUME['name']: fake_lun}

        self.library._extend_volume(fake.VOLUME, new_size, 'fake_qos_policy')

        mock_get_lun_from_table.assert_called_once_with(fake.VOLUME['name'])
        mock_get_lun_geometry.assert_called_once_with(
            fake.LUN_METADATA['Path'])
        self.assertFalse(mock_do_direct_resize.called)
        mock_do_sub_clone_resize.assert_called_once_with(
            fake.LUN_METADATA['Path'], six.text_type(new_size_bytes),
            qos_policy_group_name='fake_qos_policy')
        self.assertEqual(six.text_type(new_size_bytes),
                         self.library.lun_table[fake.VOLUME['name']].size)

    def test__extend_volume_no_change(self):

        current_size = fake.LUN_SIZE
        current_size_bytes = current_size * units.Gi
        new_size = fake.LUN_SIZE
        max_size = fake.LUN_SIZE * 10
        max_size_bytes = max_size * units.Gi
        fake_volume = copy.copy(fake.VOLUME)
        fake_volume['size'] = new_size

        fake_lun = block_base.NetAppLun(fake.LUN_HANDLE,
                                        fake.LUN_ID,
                                        current_size_bytes,
                                        fake.LUN_METADATA)
        mock_get_lun_from_table = self.mock_object(
            self.library, '_get_lun_from_table',
            mock.Mock(return_value=fake_lun))
        fake_lun_geometry = {'max_resize': six.text_type(max_size_bytes)}
        mock_get_lun_geometry = self.mock_object(
            self.library.zapi_client, 'get_lun_geometry',
            mock.Mock(return_value=fake_lun_geometry))
        mock_do_direct_resize = self.mock_object(self.library.zapi_client,
                                                 'do_direct_resize')
        mock_do_sub_clone_resize = self.mock_object(self.library,
                                                    '_do_sub_clone_resize')
        self.library.lun_table = {fake_volume['name']: fake_lun}

        self.library._extend_volume(fake_volume, new_size, 'fake_qos_policy')

        mock_get_lun_from_table.assert_called_once_with(fake_volume['name'])
        self.assertFalse(mock_get_lun_geometry.called)
        self.assertFalse(mock_do_direct_resize.called)
        self.assertFalse(mock_do_sub_clone_resize.called)

    def test_do_sub_clone_resize(self):

        fake_lun = block_base.NetAppLun(fake.LUN_HANDLE,
                                        fake.LUN_ID,
                                        fake.LUN_SIZE,
                                        fake.LUN_METADATA)
        new_lun_size = fake.LUN_SIZE * 10
        new_lun_name = 'new-%s' % fake.LUN_NAME
        block_count = fake.LUN_SIZE * units.Gi / 512

        mock_get_lun_from_table = self.mock_object(
            self.library, '_get_lun_from_table',
            mock.Mock(return_value=fake_lun))
        mock_get_vol_option = self.mock_object(
            self.library, '_get_vol_option', mock.Mock(return_value='off'))
        mock_get_lun_block_count = self.mock_object(
            self.library, '_get_lun_block_count',
            mock.Mock(return_value=block_count))
        mock_create_lun = self.mock_object(
            self.library.zapi_client, 'create_lun')
        mock_clone_lun = self.mock_object(self.library, '_clone_lun')
        mock_post_sub_clone_resize = self.mock_object(
            self.library, '_post_sub_clone_resize')
        mock_destroy_lun = self.mock_object(
            self.library.zapi_client, 'destroy_lun')

        self.library._do_sub_clone_resize(fake.LUN_PATH,
                                          new_lun_size,
                                          fake.QOS_POLICY_GROUP_NAME)

        mock_get_lun_from_table.assert_called_once_with(fake.LUN_NAME)
        mock_get_vol_option.assert_called_once_with('vol0', 'compression')
        mock_get_lun_block_count.assert_called_once_with(fake.LUN_PATH)
        mock_create_lun.assert_called_once_with(
            'vol0', new_lun_name, new_lun_size, fake.LUN_METADATA,
            qos_policy_group_name=fake.QOS_POLICY_GROUP_NAME)
        mock_clone_lun.assert_called_once_with(
            fake.LUN_NAME, new_lun_name, block_count=block_count)
        mock_post_sub_clone_resize.assert_called_once_with(fake.LUN_PATH)
        self.assertFalse(mock_destroy_lun.called)

    def test_do_sub_clone_resize_compression_on(self):

        fake_lun = block_base.NetAppLun(fake.LUN_HANDLE,
                                        fake.LUN_ID,
                                        fake.LUN_SIZE,
                                        fake.LUN_METADATA)
        new_lun_size = fake.LUN_SIZE * 10
        block_count = fake.LUN_SIZE * units.Gi / 512

        mock_get_lun_from_table = self.mock_object(
            self.library, '_get_lun_from_table',
            mock.Mock(return_value=fake_lun))
        mock_get_vol_option = self.mock_object(
            self.library, '_get_vol_option', mock.Mock(return_value='on'))
        mock_get_lun_block_count = self.mock_object(
            self.library, '_get_lun_block_count',
            mock.Mock(return_value=block_count))
        mock_create_lun = self.mock_object(
            self.library.zapi_client, 'create_lun')
        mock_clone_lun = self.mock_object(self.library, '_clone_lun')
        mock_post_sub_clone_resize = self.mock_object(
            self.library, '_post_sub_clone_resize')
        mock_destroy_lun = self.mock_object(
            self.library.zapi_client, 'destroy_lun')

        self.assertRaises(exception.VolumeBackendAPIException,
                          self.library._do_sub_clone_resize,
                          fake.LUN_PATH,
                          new_lun_size,
                          fake.QOS_POLICY_GROUP_NAME)

        mock_get_lun_from_table.assert_called_once_with(fake.LUN_NAME)
        mock_get_vol_option.assert_called_once_with('vol0', 'compression')
        self.assertFalse(mock_get_lun_block_count.called)
        self.assertFalse(mock_create_lun.called)
        self.assertFalse(mock_clone_lun.called)
        self.assertFalse(mock_post_sub_clone_resize.called)
        self.assertFalse(mock_destroy_lun.called)

    def test_do_sub_clone_resize_no_blocks(self):

        fake_lun = block_base.NetAppLun(fake.LUN_HANDLE,
                                        fake.LUN_ID,
                                        fake.LUN_SIZE,
                                        fake.LUN_METADATA)
        new_lun_size = fake.LUN_SIZE * 10
        block_count = 0

        mock_get_lun_from_table = self.mock_object(
            self.library, '_get_lun_from_table',
            mock.Mock(return_value=fake_lun))
        mock_get_vol_option = self.mock_object(
            self.library, '_get_vol_option', mock.Mock(return_value='off'))
        mock_get_lun_block_count = self.mock_object(
            self.library, '_get_lun_block_count',
            mock.Mock(return_value=block_count))
        mock_create_lun = self.mock_object(
            self.library.zapi_client, 'create_lun')
        mock_clone_lun = self.mock_object(self.library, '_clone_lun')
        mock_post_sub_clone_resize = self.mock_object(
            self.library, '_post_sub_clone_resize')
        mock_destroy_lun = self.mock_object(
            self.library.zapi_client, 'destroy_lun')

        self.assertRaises(exception.VolumeBackendAPIException,
                          self.library._do_sub_clone_resize,
                          fake.LUN_PATH,
                          new_lun_size,
                          fake.QOS_POLICY_GROUP_NAME)

        mock_get_lun_from_table.assert_called_once_with(fake.LUN_NAME)
        mock_get_vol_option.assert_called_once_with('vol0', 'compression')
        mock_get_lun_block_count.assert_called_once_with(fake.LUN_PATH)
        self.assertFalse(mock_create_lun.called)
        self.assertFalse(mock_clone_lun.called)
        self.assertFalse(mock_post_sub_clone_resize.called)
        self.assertFalse(mock_destroy_lun.called)

    def test_do_sub_clone_resize_create_error(self):

        fake_lun = block_base.NetAppLun(fake.LUN_HANDLE,
                                        fake.LUN_ID,
                                        fake.LUN_SIZE,
                                        fake.LUN_METADATA)
        new_lun_size = fake.LUN_SIZE * 10
        new_lun_name = 'new-%s' % fake.LUN_NAME
        block_count = fake.LUN_SIZE * units.Gi / 512

        mock_get_lun_from_table = self.mock_object(
            self.library, '_get_lun_from_table',
            mock.Mock(return_value=fake_lun))
        mock_get_vol_option = self.mock_object(
            self.library, '_get_vol_option', mock.Mock(return_value='off'))
        mock_get_lun_block_count = self.mock_object(
            self.library, '_get_lun_block_count',
            mock.Mock(return_value=block_count))
        mock_create_lun = self.mock_object(
            self.library.zapi_client, 'create_lun',
            mock.Mock(side_effect=netapp_api.NaApiError))
        mock_clone_lun = self.mock_object(self.library, '_clone_lun')
        mock_post_sub_clone_resize = self.mock_object(
            self.library, '_post_sub_clone_resize')
        mock_destroy_lun = self.mock_object(
            self.library.zapi_client, 'destroy_lun')

        self.assertRaises(netapp_api.NaApiError,
                          self.library._do_sub_clone_resize,
                          fake.LUN_PATH,
                          new_lun_size,
                          fake.QOS_POLICY_GROUP_NAME)

        mock_get_lun_from_table.assert_called_once_with(fake.LUN_NAME)
        mock_get_vol_option.assert_called_once_with('vol0', 'compression')
        mock_get_lun_block_count.assert_called_once_with(fake.LUN_PATH)
        mock_create_lun.assert_called_once_with(
            'vol0', new_lun_name, new_lun_size, fake.LUN_METADATA,
            qos_policy_group_name=fake.QOS_POLICY_GROUP_NAME)
        self.assertFalse(mock_clone_lun.called)
        self.assertFalse(mock_post_sub_clone_resize.called)
        self.assertFalse(mock_destroy_lun.called)

    def test_do_sub_clone_resize_clone_error(self):

        fake_lun = block_base.NetAppLun(fake.LUN_HANDLE,
                                        fake.LUN_ID,
                                        fake.LUN_SIZE,
                                        fake.LUN_METADATA)
        new_lun_size = fake.LUN_SIZE * 10
        new_lun_name = 'new-%s' % fake.LUN_NAME
        new_lun_path = '/vol/vol0/%s' % new_lun_name
        block_count = fake.LUN_SIZE * units.Gi / 512

        mock_get_lun_from_table = self.mock_object(
            self.library, '_get_lun_from_table',
            mock.Mock(return_value=fake_lun))
        mock_get_vol_option = self.mock_object(
            self.library, '_get_vol_option', mock.Mock(return_value='off'))
        mock_get_lun_block_count = self.mock_object(
            self.library, '_get_lun_block_count',
            mock.Mock(return_value=block_count))
        mock_create_lun = self.mock_object(
            self.library.zapi_client, 'create_lun')
        mock_clone_lun = self.mock_object(
            self.library, '_clone_lun',
            mock.Mock(side_effect=netapp_api.NaApiError))
        mock_post_sub_clone_resize = self.mock_object(
            self.library, '_post_sub_clone_resize')
        mock_destroy_lun = self.mock_object(
            self.library.zapi_client, 'destroy_lun')

        self.assertRaises(netapp_api.NaApiError,
                          self.library._do_sub_clone_resize,
                          fake.LUN_PATH,
                          new_lun_size,
                          fake.QOS_POLICY_GROUP_NAME)

        mock_get_lun_from_table.assert_called_once_with(fake.LUN_NAME)
        mock_get_vol_option.assert_called_once_with('vol0', 'compression')
        mock_get_lun_block_count.assert_called_once_with(fake.LUN_PATH)
        mock_create_lun.assert_called_once_with(
            'vol0', new_lun_name, new_lun_size, fake.LUN_METADATA,
            qos_policy_group_name=fake.QOS_POLICY_GROUP_NAME)
        mock_clone_lun.assert_called_once_with(
            fake.LUN_NAME, new_lun_name, block_count=block_count)
        self.assertFalse(mock_post_sub_clone_resize.called)
        mock_destroy_lun.assert_called_once_with(new_lun_path)

    def test_configure_chap_generate_username_and_password(self):
        """Ensure that a CHAP username and password are generated."""
        initiator_name = fake.ISCSI_CONNECTOR['initiator']

        username, password = self.library._configure_chap(initiator_name)

        self.assertEqual(na_utils.DEFAULT_CHAP_USER_NAME, username)
        self.assertIsNotNone(password)
        self.assertEqual(len(password), na_utils.CHAP_SECRET_LENGTH)

    def test_add_chap_properties(self):
        """Ensure that CHAP properties are added to the properties dictionary

        """
        properties = {'data': {}}
        self.library._add_chap_properties(properties, 'user1', 'pass1')

        data = properties['data']
        self.assertEqual('CHAP', data['auth_method'])
        self.assertEqual('user1', data['auth_username'])
        self.assertEqual('pass1', data['auth_password'])
        self.assertEqual('CHAP', data['discovery_auth_method'])
        self.assertEqual('user1', data['discovery_auth_username'])
        self.assertEqual('pass1', data['discovery_auth_password'])

    def test_create_cgsnapshot(self):
        snapshot = fake.CG_SNAPSHOT
        snapshot['volume'] = fake.CG_VOLUME

        mock_extract_host = self.mock_object(
            volume_utils, 'extract_host',
            mock.Mock(return_value=fake.POOL_NAME))

        mock_clone_lun = self.mock_object(self.library, '_clone_lun')
        mock_busy = self.mock_object(self.library, '_handle_busy_snapshot')

        self.library.create_cgsnapshot(fake.CG_SNAPSHOT, [snapshot])

        mock_extract_host.assert_called_once_with(fake.CG_VOLUME['host'],
                                                  level='pool')
        self.zapi_client.create_cg_snapshot.assert_called_once_with(
            set([fake.POOL_NAME]), fake.CG_SNAPSHOT_ID)
        mock_clone_lun.assert_called_once_with(
            fake.CG_VOLUME_NAME, fake.CG_SNAPSHOT_NAME,
            source_snapshot=fake.CG_SNAPSHOT_ID)
        mock_busy.assert_called_once_with(fake.POOL_NAME, fake.CG_SNAPSHOT_ID)

    def test_delete_cgsnapshot(self):

        mock_delete_snapshot = self.mock_object(
            self.library, '_delete_lun')

        self.library.delete_cgsnapshot(fake.CG_SNAPSHOT, [fake.CG_SNAPSHOT])

        mock_delete_snapshot.assert_called_once_with(fake.CG_SNAPSHOT['name'])

    def test_delete_cgsnapshot_not_found(self):
        self.mock_object(block_base, 'LOG')
        self.mock_object(self.library, '_get_lun_attr',
                         mock.Mock(return_value=None))

        self.library.delete_cgsnapshot(fake.CG_SNAPSHOT, [fake.CG_SNAPSHOT])

        self.assertEqual(0, block_base.LOG.error.call_count)
        self.assertEqual(1, block_base.LOG.warning.call_count)
        self.assertEqual(0, block_base.LOG.info.call_count)

    def test_create_volume_with_cg(self):
        volume_size_in_bytes = int(fake.CG_VOLUME_SIZE) * units.Gi
        self._create_volume_test_helper()

        self.library.create_volume(fake.CG_VOLUME)

        self.library._create_lun.assert_called_once_with(
            fake.POOL_NAME, fake.CG_VOLUME_NAME, volume_size_in_bytes,
            fake.CG_LUN_METADATA, None)
        self.assertEqual(0, self.library.
                         _mark_qos_policy_group_for_deletion.call_count)
        self.assertEqual(0, block_base.LOG.error.call_count)

    def _create_volume_test_helper(self):
        self.mock_object(na_utils, 'get_volume_extra_specs')
        self.mock_object(na_utils, 'log_extra_spec_warnings')
        self.mock_object(block_base, 'LOG')
        self.mock_object(volume_utils, 'extract_host',
                         mock.Mock(return_value=fake.POOL_NAME))
        self.mock_object(self.library, '_setup_qos_for_volume',
                         mock.Mock(return_value=None))
        self.mock_object(self.library, '_create_lun')
        self.mock_object(self.library, '_create_lun_handle')
        self.mock_object(self.library, '_add_lun_to_table')
        self.mock_object(self.library, '_mark_qos_policy_group_for_deletion')

    def test_create_consistency_group(self):
        model_update = self.library.create_consistencygroup(
            fake.CONSISTENCY_GROUP)
        self.assertEqual('available', model_update['status'])

    def test_delete_consistencygroup_volume_delete_failure(self):
        self.mock_object(block_base, 'LOG')
        self.mock_object(self.library, '_delete_lun',
                         mock.Mock(side_effect=Exception))

        model_update, volumes = self.library.delete_consistencygroup(
            fake.CONSISTENCY_GROUP, [fake.CG_VOLUME])

        self.assertEqual('deleted', model_update['status'])
        self.assertEqual('error_deleting', volumes[0]['status'])
        self.assertEqual(1, block_base.LOG.exception.call_count)

    def test_delete_consistencygroup_not_found(self):
        self.mock_object(block_base, 'LOG')
        self.mock_object(self.library, '_get_lun_attr',
                         mock.Mock(return_value=None))

        model_update, volumes = self.library.delete_consistencygroup(
            fake.CONSISTENCY_GROUP, [fake.CG_VOLUME])

        self.assertEqual(0, block_base.LOG.error.call_count)
        self.assertEqual(1, block_base.LOG.warning.call_count)
        self.assertEqual(0, block_base.LOG.info.call_count)

        self.assertEqual('deleted', model_update['status'])
        self.assertEqual('deleted', volumes[0]['status'])

    def test_create_consistencygroup_from_src_cg_snapshot(self):

        mock_clone_source_to_destination = self.mock_object(
            self.library, '_clone_source_to_destination')

        self.library.create_consistencygroup_from_src(
            fake.CONSISTENCY_GROUP, [fake.VOLUME], cgsnapshot=fake.CG_SNAPSHOT,
            snapshots=[fake.CG_VOLUME_SNAPSHOT])

        clone_source_to_destination_args = {
            'name': fake.CG_SNAPSHOT['name'],
            'size': fake.CG_SNAPSHOT['volume_size'],
        }
        mock_clone_source_to_destination.assert_called_once_with(
            clone_source_to_destination_args, fake.VOLUME)

    def test_create_consistencygroup_from_src_cg(self):
        class fake_lun_name(object):
            pass
        fake_lun_name_instance = fake_lun_name()
        fake_lun_name_instance.name = fake.SOURCE_CG_VOLUME['name']
        self.mock_object(self.library, '_get_lun_from_table', mock.Mock(
            return_value=fake_lun_name_instance)
        )
        mock_clone_source_to_destination = self.mock_object(
            self.library, '_clone_source_to_destination')

        self.library.create_consistencygroup_from_src(
            fake.CONSISTENCY_GROUP, [fake.VOLUME],
            source_cg=fake.SOURCE_CONSISTENCY_GROUP,
            source_vols=[fake.SOURCE_CG_VOLUME])

        clone_source_to_destination_args = {
            'name': fake.SOURCE_CG_VOLUME['name'],
            'size': fake.SOURCE_CG_VOLUME['size'],
        }
        mock_clone_source_to_destination.assert_called_once_with(
            clone_source_to_destination_args, fake.VOLUME)

    def test_handle_busy_snapshot(self):
        self.mock_object(block_base, 'LOG')
        mock_get_snapshot = self.mock_object(
            self.zapi_client, 'get_snapshot',
            mock.Mock(return_value=fake.SNAPSHOT)
        )

        self.library._handle_busy_snapshot(fake.FLEXVOL, fake.SNAPSHOT_NAME)

        self.assertEqual(1, block_base.LOG.info.call_count)
        mock_get_snapshot.assert_called_once_with(fake.FLEXVOL,
                                                  fake.SNAPSHOT_NAME)
