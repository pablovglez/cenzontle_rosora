
AGENT_XML = """  
<node>
  <interface name="org.bluez.Agent1">
    <method name="RequestPasskey">
      <arg type="o" name="device" direction="in"/>
      <arg type="u" name="passkey" direction="out"/>
    </method>
    <method name="Cancel"/>
    <method name="Release"/>
  </interface>
</node>
"""


class FixedPasskeyAgent:
    dbus = AGENT_XML

    def __init__(self, passkey: int):
        self._passkey = passkey

    def RequestPasskey(self, device):
        return self._passkey

    def Cancel(self): pass

    def Release(self): pass