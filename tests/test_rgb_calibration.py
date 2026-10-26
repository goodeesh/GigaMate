from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import usb.core

from gigamate import cli, profiles, protocol


@pytest.fixture
def device():
    dev = MagicMock()
    dev.get_active_configuration.return_value = [
        SimpleNamespace(bInterfaceNumber=i) for i in range(5)
    ]
    dev.is_kernel_driver_active.return_value = True
    return dev


@pytest.mark.parametrize("interface", [None, 4])
def test_calibration_discovers_and_saves_interface_four(device, interface):
    def transfer(_request_type, _request, _value, iface, _data):
        if iface != 4:
            raise usb.core.USBError("Pipe error")
        return 8

    device.ctrl_transfer.side_effect = transfer
    with patch("gigamate.profiles.usb.util.claim_interface"), \
         patch("gigamate.profiles.usb.util.release_interface"), \
         patch("gigamate.profiles.time.sleep"), \
         patch("builtins.input", side_effect=["red", "done", "y", "y", "Test"]):
        profile = profiles.calibrate(device, 0x0414, 0x8104, interface=interface)
    assert profile.control_interface == 4
    assert profile.interfaces == [4]
    assert profile.colour_map["red"][2] == (1, 100)
    probed = {call.args[3] for call in device.ctrl_transfer.call_args_list}
    assert probed == ({4} if interface == 4 else set(range(5)))
    # Every driver detached during probing or calibration is restored.
    assert device.detach_kernel_driver.call_args_list == device.attach_kernel_driver.call_args_list


def test_calibration_rejects_missing_interface(device):
    assert profiles.calibrate(device, 0x0414, 0x8104, interface=8) is None
    device.detach_kernel_driver.assert_not_called()
    device.ctrl_transfer.assert_not_called()


def test_calibration_explains_usb_permissions(device, capsys):
    device.get_active_configuration.side_effect = usb.core.USBError("Access denied")
    assert profiles.calibrate(device, 0x0414, 0x8104) is None
    assert "hidraw permissions alone are insufficient" in capsys.readouterr().out


@pytest.mark.parametrize("error", [KeyboardInterrupt, EOFError])
def test_calibration_restores_driver_when_interrupted(device, error):
    with patch("gigamate.profiles.usb.util.claim_interface"), \
         patch("gigamate.profiles.usb.util.release_interface") as release, \
         patch("gigamate.profiles.time.sleep"), \
         patch("builtins.input", side_effect=error):
        with pytest.raises(error):
            profiles.calibrate(device, 0x0414, 0x8104, interface=4)
    assert device.attach_kernel_driver.call_count == 2
    release.assert_called_with(device, 4)


def test_runtime_detaches_saved_profile_interface(device):
    profile = profiles.DeviceProfile(vid=0x0414, pid=0x8104,
                                     interfaces=[4], control_interface=4)
    with patch("gigamate.protocol.usb.core.find", return_value=device):
        assert protocol.get_keyboard(profile=profile) is device
    device.detach_kernel_driver.assert_called_once_with(4)


@pytest.mark.parametrize("arguments", [
    ["calibrate", "rgb", "--interface", "4"],
    ["rgb", "calibrate", "-i", "4"],
    ["calibrate", "all", "--interface", "4"],
])
def test_cli_forwards_interface_without_detaching_default_drivers(device, arguments):
    with patch("sys.argv", ["gigamate"] + arguments), \
         patch("gigamate.cli.detect_device", return_value=(0x0414, 0x8104)), \
         patch("gigamate.cli.resolve_profile", return_value=None), \
         patch("gigamate.cli.get_keyboard", return_value=device) as get_keyboard, \
         patch("gigamate.cli.run_calibrate", return_value=None) as calibrate, \
         patch("gigamate.cli.cmd_calibrate_acpi"):
        cli.main()
    get_keyboard.assert_called_once_with(0x0414, 0x8104, interfaces=[])
    calibrate.assert_called_once_with(device, 0x0414, 0x8104, interface=4)
