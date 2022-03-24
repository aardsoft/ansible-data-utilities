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
            'boolconv': self.boolconv,
        }

    def boolconv(self, value, bool_type="truefalse"):
        if (str(value).lower()=="true"):
            ivalue=True
        elif (str(value).lower()=="false"):
            ivalue=False
        elif (str(value).lower()=="yes"):
            ivalue=True
        elif (str(value).lower()=="no"):
            ivalue=False
        elif (str(value).lower()=="on"):
            ivalue=True
        elif (str(value).lower()=="off"):
            ivalue=False
        elif (str(value).lower()=="1"):
            ivalue=True
        elif (str(value).lower()=="0"):
            ivalue=False

        if (bool_type=="onoff"):
            return ("off","on")[ivalue]
        if (bool_type=="ONOFF"):
            return ("OFF","ON")[ivalue]
        if (bool_type=="truefalse"):
            return ("false","true")[ivalue]
        if (bool_type=="TrueFalse"):
            return ("False","True")[ivalue]
        if (bool_type=="TRUEFALSE"):
            return ("FALSE","TRUE")[ivalue]
        if (bool_type=="yesno"):
            return ("no","yes")[ivalue]
        if (bool_type=="YesNo"):
            return ("No","Yes")[ivalue]
        if (bool_type=="YESNO"):
            return ("NO","YES")[ivalue]
        if (bool_type=="int"):
            return ("0","1")[ivalue]
