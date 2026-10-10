"""Bluetooth for the VM.

The Qualcomm Bluetooth service cannot find its chip in a VM, and Android's Bluetooth app aborts
when it fails. Replace it with Cuttlefish's service, which speaks plain HCI over a serial port
(/dev/hvc0, a virtio console the launcher connects to a virtual or real controller).

The Bluetooth feature declarations move into a vendor SKU folder, so Android only believes it
has Bluetooth when the launcher boots with androidboot.product.vendor.sku=vmbt, which it does
only when it has a controller to attach. Without one, Bluetooth is absent rather than crashing.
"""
from pathlib import Path
R=Path(__file__).resolve().parents[1]
SKU='vmbt'
def apply(add,original,files):
 name='android.hardware.bluetooth@aidl-service-qti'
 # Same path as the service it replaces, so its SELinux domain and interface declaration carry over.
 add('bin/hw/'+name,(R/'artifacts/cuttlefish-bluetooth/android.hardware.bluetooth-service.cuttlefish').read_bytes(),'hal_bluetooth_default_exec',0o755)
 add(f'etc/init/{name}.rc',f'''# Cuttlefish HCI-over-serial Bluetooth service in place of the Qualcomm one.
service vendor.bluetooth-aidl-qti /vendor/bin/hw/{name} --serial /dev/hvc0
    class hal
    user bluetooth
    group bluetooth
    disabled

on property:ro.boot.product.vendor.sku={SKU}
    start vendor.bluetooth-aidl-qti
'''.encode(),'vendor_configs_file')
 for n in ['android.hardware.bluetooth.xml','android.hardware.bluetooth_le.xml']:
  add(f'etc/permissions/sku_{SKU}/{n}',original('etc/permissions/'+n),'vendor_configs_file')
  add('etc/permissions/'+n,b'<?xml version="1.0" encoding="utf-8"?>\n<!-- Declared in sku_'+SKU.encode()+b'/ when the VM has a Bluetooth controller. -->\n<permissions/>\n','vendor_configs_file')
 add('etc/ueventd.rc',original('etc/ueventd.rc')+b'\n# VM Bluetooth controller link\n/dev/hvc0                 0660   bluetooth  bluetooth\n','vendor_configs_file')
 n='etc/selinux/vendor_file_contexts';b,label,mode=files[n]
 files[n]=(b+b'/dev/hvc0 u:object_r:hci_attach_dev:s0\n',label,mode)
 return ['etc/permissions'],['etc/permissions/sku_'+SKU]
