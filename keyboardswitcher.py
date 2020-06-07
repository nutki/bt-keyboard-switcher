#!/usr/bin/python3

import os
import sys
import dbus
import time
import evdev
from evdev import ecodes
import keymap
from select import select
import logging
from logging import debug, info, warning, error
import pyudev
import re
import functools
import errno
import socket
from configparser import ConfigParser

logging.basicConfig(level=logging.DEBUG)

class Config:
    config_file = 'keyboardswitcher.ini'
    config = ConfigParser()
    config.read(config_file)
    @staticmethod
    def write():
        with open(Config.config_file, 'w') as fh:
            Config.config.write(fh)
    @staticmethod
    def set_dev_config(addr, index):
        if not addr in Config.config:
            Config.config.add_section(addr)
        section = Config.config[addr]
        Config.config.set(addr, 'Index', str(index + 1))
        if not 'MouseDelayMs' in section:
            Config.config.set(addr, 'MouseDelayMs', str(20))
        Config.write()
    @staticmethod
    def get_dev_config(addr):
        if not addr in Config.config:
            return {
                'index': None,
                'mouse_delay': 20,
                'mouse_speed': 1
            }
        section = Config.config[addr]
        return {
            'mouse_delay': section.getint('MouseDelayMs') if 'MouseDelayMs' in section else 20,
            'index': section.getint('Index') - 1 if 'Index' in section else None,
            'mouse_speed': section.getfloat('MouseSpeed') if 'MouseSpeed' in section else 1
        }
        
class BluetoothDevice:
    by_index = {}
    by_addr = {}
    current = 0
    connecting_sockets = []
    @staticmethod
    def get_by_address(addr):
        if addr in BluetoothDevice.by_addr:
            return BluetoothDevice.by_addr.get(addr)
        return BluetoothDevice(addr)
    @staticmethod
    def get_by_index(index):
        return BluetoothDevice.by_index.get(index)
    @staticmethod
    def alloc_index(pref_index=None):
        if pref_index != None and pref_index not in BluetoothDevice.by_index:
            return pref_index
        index = 0
        while True:
            if index not in BluetoothDevice.by_index:
                return index
            index += 1
    @staticmethod
    def get_all():
        return BluetoothDevice.by_index.values()
    @staticmethod
    def all_sockets():
        d = BluetoothDevice.by_index.values()
        return [dev.isocket for dev in d if dev.isocket ] + [dev.csocket for dev in d if dev.csocket ]
    @staticmethod
    def print():
        print ('------')
        for i in BluetoothDevice.by_index:
            dev = BluetoothDevice.by_index[i]
            print(dev)

    def __init__(self, addr):
        config = Config.get_dev_config(addr)
        self.mouse_delay = config['mouse_delay'] / 1000
        self.mouse_speed = config['mouse_speed']
        self.state = "DISCONNECTED"
        self.csocket = None
        self.isocket = None
        self.addr = addr
        self.index = BluetoothDevice.alloc_index(config['index'])
        self.ledstate = 0
        Config.set_dev_config(addr, self.index)
        BluetoothDevice.by_addr[self.addr] = self
        BluetoothDevice.by_index[self.index] = self
    def __str__(self):
        return "%s%d: %s %s" % ('*' if BluetoothDevice.current == self.index else ' ', self.index, self.addr, self.state)
    def set_isocket(self, sock):
        self.isocket = sock
        self.state = "CONNECTED" if self.csocket else "CONNECTING"
    def set_csocket(self, sock):
        self.csocket = sock
        self.state = "CONNECTED" if self.isocket else "CONNECTING"
    def del_isocket(self):
        self.isocket = None
        self.state = "DISCONNECTING" if self.csocket else "DISCONNECTED"
    def del_csocket(self):
        self.csocket = None
        self.state = "DISCONNECTING" if self.isocket else "DISCONNECTED"
    def connect(self):
        debug("Connecting to %s", self.addr)
        BluetoothDevice.connect_nonblocking((self.addr, BluetoothDeviceManager.P_CTRL))
    def send_input(self, ir):
        try:
            self.isocket.send(bytes(ir))
        except OSError as err:
            error(err)
            self.del_isocket()
    @staticmethod
    def send_all(ir):
        for i in BluetoothDevice.by_index:
            BluetoothDevice.by_index[i].send_input(ir)
    @staticmethod
    def current_dev():
        return BluetoothDevice.get_by_index(BluetoothDevice.current)
    @staticmethod
    def mouse_delay():
        dev = BluetoothDevice.current_dev()
        return dev.mouse_delay if dev else 0
    @staticmethod
    def mouse_speed():
        dev = BluetoothDevice.current_dev()
        return dev.mouse_speed if dev else 1
    @staticmethod
    def send_current(ir):
        dev = BluetoothDevice.current_dev()
        if dev and dev.isocket:
            dev.send_input(ir)
    @staticmethod
    def set_current(index):
        debug("Setting current to %d", index)
        dev = BluetoothDevice.current_dev()
        if index != BluetoothDevice.current:
            if BluetoothDevice.current == -1:
                InputDevice.grab(True)
            if index == -1:
                InputDevice.grab(False)
            if dev and dev.isocket:
                dev.send_input([0xA1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
                dev.send_input([0xA2, 2, 0, 0, 0, 0])
            BluetoothDevice.current = index
            dev = BluetoothDevice.current_dev()
            InputDevice.set_leds_all(dev.ledstate if dev != None else 0)
        if dev and dev.state == "DISCONNECTED":
            dev.connect()
    @staticmethod
    def connect_nonblocking(addr):
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        sock.setblocking(0)
        try:
            sock.connect(addr)
        except OSError as err:
            if err.errno != errno.EINPROGRESS:
                error(err)
                return
        BluetoothDevice.connecting_sockets.append(sock)

    @staticmethod
    def connect_all():
        debug("Setting pairable")
        os.system("hciconfig hci0 piscan")

class BluetoothDeviceManager:
    P_CTRL = 17
    P_INTR = 19

    def __init__(self):
        os.system("hciconfig hci0 class 0x0025C0")
        os.system("hciconfig hci0 name Pi\ Keyboard/Mouse")

        self.bus = dbus.SystemBus()
        self.manager = dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez"),
                                        "org.bluez.ProfileManager1")
        fh = open(sys.path[0] + "/sdp_record.xml", "r")
        self.service_record = fh.read()
        fh.close()
        opts = { "AutoConnect": True, "ServiceRecord": self.service_record }
        self.manager.RegisterProfile("/org/bluez/hci0", "00001124-0000-1000-8000-00805f9b34fb", opts)

        self.scontrol = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP) #BluetoothSocket(L2CAP)
        self.sinterrupt = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP) #BluetoothSocket(L2CAP)
        self.scontrol.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sinterrupt.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.scontrol.bind((socket.BDADDR_ANY, BluetoothDeviceManager.P_CTRL))
        self.sinterrupt.bind((socket.BDADDR_ANY, BluetoothDeviceManager.P_INTR))
        self.scontrol.listen(5)
        self.sinterrupt.listen(5)

hotkeys = {
    (keymap.keymap[ecodes.KEY_ESC] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.connect_all(),
    (keymap.keymap[ecodes.KEY_F1] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(0),
    (keymap.keymap[ecodes.KEY_F2] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(1),
    (keymap.keymap[ecodes.KEY_F3] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(2),
    (keymap.keymap[ecodes.KEY_F4] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(3),
    (keymap.keymap[ecodes.KEY_F5] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(4),
    (keymap.keymap[ecodes.KEY_F6] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(5),
    (keymap.keymap[ecodes.KEY_F7] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(6),
    (keymap.keymap[ecodes.KEY_F8] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(7),
    (keymap.keymap[ecodes.KEY_F9] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(8),
    (keymap.keymap[ecodes.KEY_F10] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(9),
    (keymap.keymap[ecodes.KEY_F11] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(10),
    (keymap.keymap[ecodes.KEY_F12] + 256 * keymap.modkeymap[ecodes.KEY_LEFTCTRL]): lambda: BluetoothDevice.set_current(-1)
}


class InputDevice():
    inputs = []
    @staticmethod
    def init():
        context = pyudev.Context()
        devs = context.list_devices(subsystem="input")
        InputDevice.monitor = pyudev.Monitor.from_netlink(context)
        InputDevice.monitor.filter_by(subsystem='input')
        InputDevice.monitor.start()
        for d in [*devs]:
            InputDevice.add_device(d)
    @staticmethod
    def add_device(dev):
        if dev.device_node == None or not re.match(".*/event\\d+", dev.device_node):
            return
        try:
            if "ID_INPUT_KEY" in dev.properties:
                InputDevice.inputs.append(KeyboardInput(dev.device_node))
            if "ID_INPUT_MOUSE" in dev.properties:
                InputDevice.inputs.append(MouseInput(dev.device_node))
        except OSError:
            error("Failed to connect to %s", dev.device_node)
    @staticmethod
    def remove_device(dev):
        if dev.device_node == None or not re.match(".*/event\\d+", dev.device_node):
            return
        InputDevice.inputs = list(filter(lambda i: i.device_node != dev.device_node, InputDevice.inputs))
        info("Disconnected %s", dev)
    @staticmethod
    def set_leds_all(ledvalue):
        for dev in InputDevice.inputs:
            dev.set_leds(ledvalue)
    @staticmethod
    def grab(on):
        if on:
            debug("Grabbing all input devices")
            for dev in InputDevice.inputs:
                dev.device.grab()
        else:
            debug("Releasing all input devices")
            for dev in InputDevice.inputs:
                dev.device.ungrab()
    def __init__(self, device_node):
        self.device_node = device_node
        self.device = evdev.InputDevice(device_node)
        self.device.grab()
        info("Connected %s", self)
    def fileno(self):
        return self.device.fd
    def __str__(self):
        return "%s@%s (%s)" % (self.__class__.__name__, self.device_node, self.device.name)
class KeyboardInput(InputDevice):
    def __init__(self, device_node):
        super().__init__(device_node)
        self.state = [ 0xA1, 1, 0, 0, 0, 0, 0, 0, 0, 0 ]
        self.set_leds(0)
    def set_leds(self, ledvalue):
        for i in range(5):
            self.device.set_led(i, 1 if ledvalue & (1<<i) else 0)
    def change_state(self, event):
        if event.type != ecodes.EV_KEY or event.value > 1:
            return
        debug(ecodes.KEY[event.code])
        modkey_element = keymap.modkeymap.get(event.code)
        pressed_code = 0
        if modkey_element != None:
            if event.value == 1:
                self.state[2] |= modkey_element
            else:
                self.state[2] &= ~modkey_element
        else:
            hex_key = keymap.keymap.get(event.code)
            if hex_key == None:
                warning("Unknown evdev key code %d", event.code)
                return
            if event.value == 1 and sum(self.state[4:]) == 0:
                pressed_code = self.state[2] * 256 + hex_key
            for i in range (4, 10):
                if self.state[i] == hex_key and event.value == 0:
                    self.state[i] = 0x00
                elif self.state[i] == 0x00 and event.value == 1:
                    self.state[i] = hex_key
                    break
        if pressed_code in hotkeys:
            hotkeys[pressed_code]()
        else:
            BluetoothDevice.send_current(self.state)
class MouseInput(InputDevice):
    def __init__(self, device_node):
        super().__init__(device_node)
        self.state = [ 0xA1, 2, 0, 0, 0, 0 ]
        self.x = 0
        self.y = 0
        self.z = 0
        self.change = False
        self.last = 0
    def change_state(self, event):
        if event.type == ecodes.EV_SYN:
            current = time.monotonic()
            if current - self.last < BluetoothDevice.mouse_delay() and not self.change:
                return
            # print(current - self.last,self.x,self.y,self.z)
            self.last = current
            speed = BluetoothDevice.mouse_speed()
            self.state[3] = min(127, max(-127, int(self.x * speed))) & 255
            self.state[4] = min(127, max(-127, int(self.y * speed))) & 255
            self.state[5] = min(127, max(-127, self.z)) & 255
            self.x = 0
            self.y = 0
            self.z = 0
            self.change = False
            BluetoothDevice.send_current(self.state)
        if event.type == ecodes.EV_KEY:
            print(self, event.code, event.value, "KEY")
            self.change = True
            if event.code >= 272 and event.code <= 276 and event.value < 2:
                button_no = event.code - 272
                if event.value == 1:
                    self.state[2] |= 1 << button_no
                else:
                    self.state[2] &= ~(1 << button_no)
        if event.type == ecodes.EV_REL:
#            print(self, event.code, event.value, "REL")
            if event.code == 0:
                self.x += event.value
            if event.code == 1:
                self.y += event.value
            if event.code == 8:
                self.z += event.value
    def set_leds(self, ledvalue):
        pass

def event_loop(bt):
    while True:
        desctiptors = [*InputDevice.inputs, InputDevice.monitor, bt.scontrol, bt.sinterrupt, *BluetoothDevice.all_sockets()]
        wdescriptors = BluetoothDevice.connecting_sockets
        r, w, x = select(desctiptors, wdescriptors, [])
        for sock in w:
            try:
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err:
                    raise OSError(err, os.strerror(err))
                addr = sock.getpeername()
                debug("Connected: %s", addr)
                dev = BluetoothDevice.get_by_address(addr[0])
                if addr[1] == BluetoothDeviceManager.P_CTRL:
                    dev.set_csocket(sock)
                    BluetoothDevice.connect_nonblocking((addr[0], BluetoothDeviceManager.P_INTR))
                if addr[1] == BluetoothDeviceManager.P_INTR:
                    dev.set_isocket(sock)
            except OSError as err:
                warning("Connection error: %s", err)
            BluetoothDevice.connecting_sockets.remove(sock)
        if InputDevice.monitor in r:
            for d in iter(functools.partial(InputDevice.monitor.poll, 0), None):
                if d.action == 'add':
                    InputDevice.add_device(d)
                if d.action == 'remove':
                    InputDevice.remove_device(d)
        for i in InputDevice.inputs:
            if i in r:
                try:
                    for event in i.device.read():
                        i.change_state(event)
                except OSError as err:
                    if err.errno == errno.ENODEV:
                        InputDevice.remove_device(i)
                    warning(err)
        for dev in BluetoothDevice.get_all():
            sock = dev.csocket
            if sock in r:
                debug("DAT @ %d", sock.fileno())
                try:
                    data = sock.recv(1024)
                    if not len(data):
                        dev.del_csocket()
                    debug("DAT %s", data.hex())
                    if data == bytes([0x71]):
                        debug("OK")
                        sock.send(bytes([0]))
                except OSError as err:
                    debug(err)
                    dev.del_csocket()
            sock = dev.isocket
            if sock in r:
                try:
                    data = sock.recv(1024)
                    if not len(data):
                        dev.del_isocket()
                    debug("DATINT %s", data.hex())
                    if data[0:2] == bytes([0xa2,0x01]):
                        dev.ledstate = data[2]
                        InputDevice.set_leds_all(data[2])
                except OSError as err:
                    debug(err)
                    dev.del_isocket()

        if bt.sinterrupt in r:
            newsock, addr = bt.sinterrupt.accept()
            newsock.setblocking(1)
            BluetoothDevice.get_by_address(addr[0]).set_isocket(newsock)
            BluetoothDevice.print()
            info("INT %d %s", newsock.fileno(), addr)
        if bt.scontrol in r:
            newsock, addr = bt.scontrol.accept()
            newsock.setblocking(1)
            BluetoothDevice.get_by_address(addr[0]).set_csocket(newsock)
            BluetoothDevice.print()
            info("CTL %d %s", newsock.fileno(), addr)

if __name__ == "__main__":
    try:
        if not os.geteuid() == 0:
            sys.exit("Run as root")

        for addr in Config.config.sections():
            BluetoothDevice(addr)
        bt = BluetoothDeviceManager()
        InputDevice.init()

        event_loop(bt)
    except KeyboardInterrupt:
        sys.exit()
