# Bluetooth Keyboard and Mouse switcher

This script is based on some of many variations of bluetooth keyboard emulation in Python. I tied to get it to a usability level
at which I can use it to act as a K(V)M switch replacement. Some of the feaures introcuded:

* Support multiple connected bluetooth devices and hotkeys for switching between them
* Handle mouse input emultion
* Reconnect to paired devices
* Handle any number of input devices as the emulated events source (with hot-swapping)
* Handle keyboard LEDs

## Installation

Tested on Rasbperry Pi OS 10 on Rasperry Pi 3B+ and Zero W.

### Install required system packages
```
sudo apt install git python3-dbus python3-pyudev python3-evdev
```
### Download this repository
```
git clone https://github.com/nutki/bt-keyboard-switcher
```
### Disable bluetooth input device client
As far as I can tell there is no way for the defult bluetooth daemon in Rasperry Pi OS to be input device client and server at the same
time. This means the client service has to be turned off first to accept connetions.

Rasperry Pi OS 10 uses `systemd` to start the bluetooth service and the only way to disable the input plugin is to use a command line
option. This means modifying `/lib/systemd/system/bluetooth.service` line:
```
ExecStart=/usr/lib/bluetooth/bluetoothd
```
to:
```
ExecStart=/usr/lib/bluetooth/bluetoothd --noplugin=input
```
And for the change to take an immediate effect:
```
sudo systemctl daemon-reload
sudo systemctl restart bluetooth
```
### Disable mouse event throttling (optional)
Raspberry Pi OS throttles the mouse event rate at the HID level. This is not compatible with some mice (including Microsoft Sculpt ergonomic mouse I own) which causes mouse pointer lag. It is safe to disable it if you don't use grphical interface as this script does
its own mouse movement event throttling anyway. To do so modify `/boot/cmdline.txt` adding the `usbhid.mousepoll=0` option.

# Usage
* Start the script
```
cd bt-keyboard-switcher
sudo ./keyboardswitcher.py
```
* Press `LCtrl + Esc` (on a keyboad physically connected to Raspberry Pi) this will make Raspberry Pi discoverable as "Pi Keyboard/Mouse".
* Pair any BT clients (tested with iOS, Android, Windows 10, and Ubuntu Linux)
* To switch between connected devices you can press `LCtrl + F1`, `LCtrl + F2`, etc. This will also trigger connect attempt if
a previously paired device got disconnected.
* Paired device information is stored in the `keyboardswitecher.ini` file, which can be edited to reassign the device order or remove a paired device.
