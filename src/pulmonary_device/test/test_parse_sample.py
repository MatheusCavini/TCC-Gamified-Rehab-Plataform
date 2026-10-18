import pytest

from pulmonary_device.pulmonary_device_node import parse_sample


@pytest.mark.parametrize('line, expected', [
    ('512', 512.0),
    ('512.75\r\n', 512.75),
    ('-3.5', -3.5),
    ('P: 512', 512.0),
    ('1.5e2', 150.0),
    ('  42  ', 42.0),
])
def test_single_value_lines(line, expected):
    assert parse_sample(line) == expected


@pytest.mark.parametrize('line', ['', '\n', 'ready', 'nan', 'inf'])
def test_lines_without_a_number_are_discarded(line):
    assert parse_sample(line) is None


def test_field_index_selects_among_several_numbers():
    assert parse_sample('t=1000 val=37', field_index=0) == 1000.0
    assert parse_sample('t=1000 val=37', field_index=1) == 37.0
    assert parse_sample('t=1000 val=37', field_index=-1) == 37.0
    assert parse_sample('only 5', field_index=3) is None
