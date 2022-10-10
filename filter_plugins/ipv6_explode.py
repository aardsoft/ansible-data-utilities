import ipaddress

class FilterModule(object):
    def filters(self):
        ''' boolconv takes a bool in the forms yes/no, true/false, on/off
            or 0/1 and converts it to a string representation based on its
            additional argument:  onoff, ONOFF, truefalse, TrueFalse, TRUEFALSE,
            yesno, YesNo, YESNO or int.
            This is mainly useful for templates where a specific format is
            required, but either user input or ansible auto conversion (or
            both) does not provide a reliable output format. '''
        return {
            'ipv6_explode': self.ipv6_explode,
        }

    def ipv6_explode(self, address):
        _address = ipaddress.IPv6Interface(address)
        return _address.ip.exploded
