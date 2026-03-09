"""Unit tests for the Ansible filter plugins.

boolconv  – converts bool-like values to various string representations
ipv6_explode – expands abbreviated IPv6 addresses to full notation
"""
import pytest
from boolconv import FilterModule as BoolconvModule
from ipv6_explode import FilterModule as Ipv6Module


@pytest.fixture(scope='module')
def boolconv():
    return BoolconvModule().boolconv


@pytest.fixture(scope='module')
def ipv6_explode():
    return Ipv6Module().ipv6_explode


# ---------------------------------------------------------------------------
# boolconv
# ---------------------------------------------------------------------------

class TestBoolconv:
    # --- truthy input recognition ---

    @pytest.mark.parametrize('value', [True, 'true', 'True', 'TRUE', 'yes',
                                        'Yes', 'YES', 'on', 'On', 'ON',
                                        '1', 'enabled', 'Enabled', 'ENABLED'])
    def test_truthy_inputs_default_type(self, boolconv, value):
        assert boolconv(value) == 'true'

    @pytest.mark.parametrize('value', [False, 'false', 'False', 'FALSE', 'no',
                                        'No', 'NO', 'off', 'Off', 'OFF',
                                        '0', 'disabled', 'Disabled', 'DISABLED'])
    def test_falsy_inputs_default_type(self, boolconv, value):
        assert boolconv(value) == 'false'

    # --- output format variations ---

    def test_onoff_true(self, boolconv):
        assert boolconv('yes', 'onoff') == 'on'

    def test_onoff_false(self, boolconv):
        assert boolconv('no', 'onoff') == 'off'

    def test_ONOFF_true(self, boolconv):
        assert boolconv(True, 'ONOFF') == 'ON'

    def test_ONOFF_false(self, boolconv):
        assert boolconv(False, 'ONOFF') == 'OFF'

    def test_TrueFalse_true(self, boolconv):
        assert boolconv('1', 'TrueFalse') == 'True'

    def test_TrueFalse_false(self, boolconv):
        assert boolconv('0', 'TrueFalse') == 'False'

    def test_TRUEFALSE_true(self, boolconv):
        assert boolconv('on', 'TRUEFALSE') == 'TRUE'

    def test_TRUEFALSE_false(self, boolconv):
        assert boolconv('off', 'TRUEFALSE') == 'FALSE'

    def test_yesno_true(self, boolconv):
        assert boolconv('enabled', 'yesno') == 'yes'

    def test_yesno_false(self, boolconv):
        assert boolconv('disabled', 'yesno') == 'no'

    def test_YesNo_true(self, boolconv):
        assert boolconv(True, 'YesNo') == 'Yes'

    def test_YesNo_false(self, boolconv):
        assert boolconv(False, 'YesNo') == 'No'

    def test_YESNO_true(self, boolconv):
        assert boolconv('true', 'YESNO') == 'YES'

    def test_YESNO_false(self, boolconv):
        assert boolconv('false', 'YESNO') == 'NO'

    def test_enableddisabled_true(self, boolconv):
        assert boolconv('on', 'enableddisabled') == 'enabled'

    def test_enableddisabled_false(self, boolconv):
        assert boolconv('off', 'enableddisabled') == 'disabled'

    def test_int_true(self, boolconv):
        assert boolconv('yes', 'int') == '1'

    def test_int_false(self, boolconv):
        assert boolconv('no', 'int') == '0'

    def test_default_type_is_truefalse(self, boolconv):
        assert boolconv(True) == 'true'
        assert boolconv(False) == 'false'

    def test_filter_registry(self):
        filters = BoolconvModule().filters()
        assert 'boolconv' in filters
        assert callable(filters['boolconv'])


# ---------------------------------------------------------------------------
# ipv6_explode
# ---------------------------------------------------------------------------

class TestIpv6Explode:
    def test_loopback(self, ipv6_explode):
        assert ipv6_explode('::1') == '0000:0000:0000:0000:0000:0000:0000:0001'

    def test_documentation_prefix(self, ipv6_explode):
        assert ipv6_explode('2001:db8::1') == '2001:0db8:0000:0000:0000:0000:0000:0001'

    def test_all_zeros(self, ipv6_explode):
        assert ipv6_explode('::') == '0000:0000:0000:0000:0000:0000:0000:0000'

    def test_full_address(self, ipv6_explode):
        addr = '2001:0db8:85a3:0000:0000:8a2e:0370:7334'
        assert ipv6_explode(addr) == addr

    def test_address_with_prefix_strips_prefix(self, ipv6_explode):
        # IPv6Interface strips the prefix; only the IP is returned
        result = ipv6_explode('2001:db8::1/64')
        assert result == '2001:0db8:0000:0000:0000:0000:0000:0001'
        assert '/' not in result

    def test_filter_registry(self):
        filters = Ipv6Module().filters()
        assert 'ipv6_explode' in filters
        assert callable(filters['ipv6_explode'])
