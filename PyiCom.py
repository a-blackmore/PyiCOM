"""
#
#  PyiCom - A python iCom implementation
#
#  A Blackmore, R Farias, 2024
#
#  https://github.com/a-blackmore/PyiCOM
#
#  V1.4 - 30/4/25
#
"""

import threading
import logging
import datetime
import time
import ctypes
import os, sys
import shutil
import toml
import tkinter as tk
from tkinter import ttk 
from tkinter import filedialog
import tkinter.scrolledtext as ScrolledText
import socket
import DCM2EFS as dcm2efs

# --- iCOM Constants and Definitions ---

# LINAC operational states (used for tracking state transitions)
states = ("UNKNOWN/INVALID",        #  0
          "PREPARATORY",            #  1
          "CONFIRM SETTINGS",       #  2
          "READY TO START",         #  3
          "SEGMENT START",          #  4
          "SEGMENT IRRADIATE",      #  5
          "SEGMENT INTERRUPT",      #  6
          "SEGMENT INTERRUPTED",    #  7
          "SEGMENT RESTART",        #  8
          "SEGMENT TERMINATE",      #  9
          "SEGMENT PAUSE",          # 10
          "FIELD TERMINATE",        # 11
          "TERMINATE CHECKING",     # 12
          "FIELD TERMINATED",       # 13
          "MOVE ONLY")              # 14

# Map of iCOM transmission tags to human-readable names
iCOM_Tags = {
    "50010001": 'MUs',
    "70010001": 'LINAC',
    "70010002": 'Patient ID',
    "70010003": 'Patient Name',
    "70010004": 'Plan Name',
    "70010005": 'Tx Name',
    "70010007": 'Beam Name',
    "70010006": 'Beam ID',
    "70020005": 'Field Complexity',
    "70020006": 'Leaf Width',
    "50010002": 'Radiation Type',
    "50010003": 'Energy',
    "50010004": 'Wedge',
    "50010007": 'Gantry Angle',
    "5001000f": 'Accessory',
    "50010019": 'Gantry Direction',
    "50010008": 'Collimator Angle',
    "500100bb": 'Collimator Direction',
    "50010009": 'X1',
    "5001000a": 'X2',
    "5001000b": 'Y1',
    "5001000c": 'Y2',
    "70020004": 'Beam Meterset'
    # Add more cases as needed
}

# Error codes returned from tag processing
iCOM_TagErrors = {
    "0" : 'OK',
    "1": 'Not Supported',
    "2": 'Under Specified',
    "3": 'Over Specified',
    "4": 'Outside Range',
    "5": 'Inconsistency',
    "6": 'Mismatch Text',
    "7": 'Protocol Error',
    "8": 'Not Ready',
    "9": 'Wrong Machine',
    "10": 'Checksum Error',
    "11": 'Version Error',
    "12": 'Not Licensed',
    "-3": 'Invalid Message'
}

# Error codes from connection-level issues
ICOM_RESULT_OK                  = 1;
INVALID_CONNECTION_HANDLE       = -2;
INVALID_MESSAGE_HANDLE          = -3;
TIMEOUT_ERROR                   = -4;
CONNECTION_IN_PROGRESS          = -5;
NOT_CONNECTED                   = -6;
INVALID_CONTROL_POINT_NUM       = -7;
DUPLICATE_ITEM                  = -8;
MISSING_CONTROL_POINT           = -9;
INVALID_PROTOCOL_VERSION        = -10;
TOO_MANY_TAGS                   = -11;
CONNECTION_FAILED               = -12;
SEND_IN_PROGRESS                = -13;
INVALID_TAG                     = -14;
ICOM_OUT_OF_MEMORY              = -15;

# Linac operating modes
THERAPY_TREATMENT_MODE          = 1;
THERAPY_CHECKRADIOGRAPH_MODE    = 3;
THERAPY_FINISH_BEAM_MODE        = 5;
QUICK_MODE_TREATMENT_MODE       = 8;
EXTERNAL_SYSTEM_VERIFY_MODE     = 9;
SERVICE_MODE                    = 10;
UNKNOWN_MODE                    = -1;

####
#### Settings & Globals
####

def getLongSeqName(seq):
    return config['sequences'][seq]['name']

conSettings = toml.load("connections.txt")  # Load saved LINAC connection data
config = toml.load("config.txt")            # Load sequence configuration

# Build initial sequence lists
origSequences = list(config['sequences'])
sequences = origSequences
sequences.sort(key = getLongSeqName)
sequencesNice = [getLongSeqName(x) for x in sequences]

# Categorize sequences by their 'type' field
sequence_types = {}
for key, val in config["sequences"].items():
    seq_type = val.get("type", "Other")
    if seq_type not in sequence_types:
        sequence_types[seq_type] = []
    sequence_types[seq_type].append(key)

# Setup connection defaults based on hostname
hostname    = socket.gethostname()
con = None
try:
    linacIP     = conSettings['connection'][hostname]['ip']
    linacName   = conSettings['connection'][hostname]['linacname']
except:
    linacIP     = "192.168.30.2"
    linacName   = "Linac ID"

# Global vars used throughout the app
statesQueue = []  # Queue of state changes received from the LINAC
statusvar = None  # Global status message displayed in GUI
fldQueue = []     # Field execution queue
fxThread = None   # FX thread for delivery control
vxThread = None   # VX thread for monitoring
guiObj = None

# Load the DLL containing iCOM functions
dirname = os.path.dirname(sys.argv[0])
os.system('cls' if os.name == 'nt' else 'clear')
iCOM = ctypes.WinDLL(dirname + "iCOMClient.dll")

# Map machine IDs to LINAC site names
linacMap = {
    "6480": "PO9"
}

####
####  Ctypes Function Definitions
####
# Define Python bindings for all iCOM DLL calls
# (iCOMConnect, iCOMSendMessage, iCOMDisconnect, etc.)
# ...

py_iCOMFXConnect = iCOM.iCOMFXConnect
py_iCOMFXConnect.restype = ctypes.c_long
py_iCOMFXConnect.argtypes = [ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]

py_iCOMVXConnect = iCOM.iCOMVXConnect
py_iCOMVXConnect.restype = ctypes.c_long
py_iCOMVXConnect.argtypes = [ctypes.c_char_p, ctypes.c_ulong]

py_iComGetConnectionState = iCOM.iCOMGetConnectionState
py_iComGetConnectionState.restype = ctypes.c_long
py_iComGetConnectionState.argtypes = [ctypes.c_long]

py_iCOMDisconnect = iCOM.iCOMDisconnect
py_iCOMDisconnect.restype = ctypes.c_long
py_iCOMDisconnect.argtypes = [ctypes.c_long]

py_iCOMBeginMessage = iCOM.iCOMBeginMessage
py_iCOMBeginMessage.restype = ctypes.c_long
py_iCOMBeginMessage.argtypes = [ctypes.c_long]

py_iCOMWaitForMessage = iCOM.iCOMWaitForMessage
py_iCOMWaitForMessage.restype = ctypes.c_long
py_iCOMWaitForMessage.argtypes = [ctypes.c_long, ctypes.c_ulong]

py_iCOMDeleteMessage = iCOM.iCOMDeleteMessage
py_iCOMDeleteMessage.restype = ctypes.c_long
py_iCOMDeleteMessage.argtypes = [ctypes.c_long]

py_iCOMSendMessage = iCOM.iCOMSendMessage
py_iCOMSendMessage.restype = ctypes.c_long
py_iCOMSendMessage.argtypes = [ctypes.c_long]

py_iCOMGetErrorCode = iCOM.iCOMGetErrorCode
py_iCOMGetErrorCode.restype = ctypes.c_short
py_iCOMGetErrorCode.argtypes = [ctypes.c_long]

py_iCOMGetErrorTag = iCOM.iCOMGetErrorTag
py_iCOMGetErrorTag.restype = ctypes.c_long
py_iCOMGetErrorTag.argtypes = [ctypes.c_long, ctypes.POINTER(ctypes.c_ulong)]

py_iCOMGetState = iCOM.iCOMGetState
py_iCOMGetState.restype = ctypes.c_short
py_iCOMGetState.argtypes = [ctypes.c_long]

#ICOMResult	    __stdcall iCOMGetTagValue       (ICOMMsgHandle messageHandle, ICOM_TAG tag, const char part, char* value);
py_iCOMGetTagValue = iCOM.iCOMGetTagValue
py_iCOMGetTagValue.restype = ctypes.c_long
py_iCOMGetTagValue.argtypes = [ctypes.c_long, ctypes.c_ulong, ctypes.c_char, ctypes.c_char_p]

py_iCOMInsertTagVal = iCOM.iCOMInsertTagVal
py_iCOMInsertTagVal.restype = ctypes.c_long
py_iCOMInsertTagVal.argtypes = [ctypes.c_long, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_ushort]

py_iCOMSendCancel = iCOM.iCOMSendCancel
py_iCOMSendCancel.restype = ctypes.c_long
py_iCOMSendCancel.argtypes = [ctypes.c_long]

py_iCOMSendConfirmEx = iCOM.iCOMSendConfirmEx
py_iCOMSendConfirmEx.restype = ctypes.c_long
py_iCOMSendConfirmEx.argtypes = [ctypes.c_long, ctypes.c_int]


def cls():
    os.system('cls' if os.name=='nt' else 'clear')


####
#### Classes
####
# (rest of the file continues with class definitions)
# FxThread: Manages sending beam sequences to LINAC
# VxThread: Monitors state changes from LINAC
# Beam: Handles beam file parsing and sending
# GUI: Tkinter-based user interface
# main(): Launches the app

class FxThread(threading.Thread):
    
    def __init__(self,  ip, linacName):
        super(FxThread, self).__init__()
        self.ip = ip
        self.linacName = linacName
        self._stop_event = threading.Event()
        self.fxHandle = None
        self.connected = False
        self.fldIndex = 0
        self.playing = False
        self.lastState = None
    
    def startPlaying(self):
        self.playing = True
    
    def stopPlaying(self):
        self.playing = False
        
    
    def printPlaylist(self):
        cls()
        if self.playing:
            print("Playing\n\n")
        else:
            print("Waiting\n\n")
        
        for idx, fld in enumerate(fldQueue):
            if idx == self.fldIndex:
                print(">\t%s" % fld['name'])
            else:
                print("\t %s" % fld['name'])
    
    def run(self):
        global statusvar
        statusvar.set("Connecting...")
        ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
        logging.info(ts + "FX connecting to Linac %s on %s..." % (self.linacName, self.ip))
        self.fxHandle = py_iCOMFXConnect(self.ip.encode('utf-8'), 1000, self.linacName.encode('utf-8'))
        global fldQueue
        global guiObj
        if self.fxHandle > 0:
            ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
            logging.info(ts + "FX Connection Established. Code %s" % self.fxHandle)
            self.connected = True
            statusvar.set("Connected")
            guiObj.playButton.config(state = tk.NORMAL)
        else:
            ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
            logging.info(ts + "Unable to Establish FX Connection. Code %s" % self.fxHandle)
            self.connected = False
            self.fxHandle = None
            statusvar.set("Connection Failed")
            guiObj.playButton.config(state = tk.NORMAL)
            return
        while self.connected:
            self.printPlaylist()
            if not self.playing:
                statusvar.set("Connected - Waiting for Fields")
                time.sleep(0.5)
            else:
                if self.fldIndex >= len(fldQueue):    # Reached the end of the Queue, reset.
                    fldQueue = []
                    self.fldIndex = 0
                else: 
                    fld = fldQueue[self.fldIndex]
                    if self.connected:
                        mu = None
                        try: 
                            mu = fld['mu']
                        except:
                            pass
                        dr = None
                        try: 
                            dr = fld['dr']
                        except:
                            pass
                        ptid = None
                        try: 
                            ptid = fld['ptid']
                        except:
                            pass
                        ptname = None
                        try: 
                            ptname = fld['ptname']
                        except:
                            pass
                        ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
                        logging.info(ts + "Field %s/%s - %s" % (self.fldIndex+1, len(fldQueue), fld['name']))
                        beam = Beam(self.fxHandle, fld['filename'], mu, dr, ptid, ptname)
                        self.sendBeam(beam)
                    else:
                        break
                    self.fldIndex = self.fldIndex + 1
    
    def waitForState(self, targetState):
        global statesQueue
        global statusvar
        ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
        #logging.info(ts + "Waiting for State: %s (%s)" % (targetState, states[targetState]))
        statusvar.set("Waiting for: %s" % (states[targetState]))
        while (self.connected) and (self.lastState != targetState):
            #print("DEBUG - WFS: %s (%s) - Playing: %s - Fld %s/%s" % (targetState, statesQueue, self.playing, self.fldIndex, len(fldQueue)))
            if (len(statesQueue) > 0):
                self.lastState = statesQueue[0]
                statesQueue.pop(0)
                if self.playing:
                    if (targetState == 2) and (self.lastState == 2):
                        iResult = py_iCOMSendConfirmEx(self.fxHandle, 1);
                else:
                    statusvar.set("Waiting for: %s - Currently: %s" % (states[targetState], states[self.lastState]))
            else:
                if (self.playing):
                    time.sleep(0.5)
                else:
                    break
    
    def cancelBeam(self):
        py_iCOMSendCancel(self.fxHandle);
    
    def sendBeam(self, beam):
        if self.connected:
            connectionState = py_iComGetConnectionState(self.fxHandle)
            if connectionState > 0:
                global statesQueue
                py_iCOMSendCancel(self.fxHandle);
                self.waitForState(1);                                # PREPARATORY
                beam.send()                
                for state in [2, 3, 5, 13]:
                    if self.playing and self.connected:
                        self.waitForState(state);
                    else:
                        #print("breaking out of Send Beam WFS loop")
                        break
            else:
                ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
                logging.info(ts + "ERROR: Connection lost. Code %s" % connectionState)
                self.connected = False
    
    def stop(self):
        if self.connected:
            global statusvar
            ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
            logging.info(ts + "Closing FX Connection.")
            py_iCOMDisconnect(self.fxHandle);
            statusvar.set("Disconnected")
            self.fxHandle = None
            self.connected = False

class VxThread(threading.Thread):
    
    def __init__(self, ip):
        super(VxThread, self).__init__()
        self.ip = ip
        self._stop_event = threading.Event()
        self.vxHandle = None
        self.connected = False
        self.lastState = None
        self.currentState = None
    
    def run(self):
        global statesQueue
        ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
        logging.info(ts + "VX connecting to %s..." % self.ip)
        self.vxHandle = py_iCOMVXConnect(self.ip.encode('utf-8'), 10000)
        if self.vxHandle > 0:
            ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
            logging.info(ts + "VX Connection Established. Code %s" % self.vxHandle)
            self.connected = True
        else:
            ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
            logging.info(ts + "Unable to Establish VX Connection. Code %s" % self.vxHandle)
            self.connected = False
            self.vxHandle = None
            return
        while self.connected:
            vxMsg = py_iCOMWaitForMessage(self.vxHandle, 10);
            if vxMsg > 0:   
                # Message Received, process it
                self.currentState = py_iCOMGetState(vxMsg)
                if self.currentState != INVALID_MESSAGE_HANDLE:
                    #print("Lat: %s\tLng: %s\tHgt: %s\t" % (self.getVal(vxMsg, int("50010012",16)), self.getVal(vxMsg, int("50010013",16)), self.getVal(vxMsg, int("50010010",16))))
                    if self.currentState != self.lastState:
                        ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
                        #logging.info(ts + "New VX State: %s (%s)" % (self.currentState, states[self.currentState]))
                        statesQueue.append(self.currentState)
                self.lastState = self.currentState
            py_iCOMDeleteMessage(vxMsg)
    
    def getVal(self, vxMsg, tag):
        val = ctypes.create_string_buffer(py_iCOMGetTagValue(vxMsg, tag, "R".encode(), None)) 
        py_iCOMGetTagValue(vxMsg, tag, "R".encode(), val)
        return val.value.decode("utf-8")
    
    def getState(self):
        return self.currentState
    
    def stop(self):
        if self.connected:
            ts = datetime.datetime.now().strftime("%H:%M:%S") + " - "
            logging.info(ts + "Closing VX Connection.")
            py_iCOMDisconnect(self.vxHandle);
            self.vxHandle = None
            self.connected = False

class Beam:
    def __init__(self, fxCon, filename = None, ovrMU = None, ovrDR = None, ovrPtID = None, ovrPtName = None):
        self.fxMsg = py_iCOMBeginMessage(fxCon);
        #if self.fxMsg > 0:
            #print("FX Message Created. Code %s" % self.fxMsg)
        #else:
            #print("ERROR: Unable to create FX message. Code: %s" % self.fxMsg)
        
        if filename:
            if filename.split(".")[-1].lower() == "efs":
                self.loadEFS(filename, ovrMU, ovrDR, ovrPtID, ovrPtName)
            elif filename.split(".")[-1].lower() == "dcm":
                efs = dcm2efs.convert_dcm2efs(filename)
                #print(efs)
            elif filename.split(".")[-1].lower() == "rtp":
                logging.info("ERROR: RTP File loading not yet implemented.")
            else:
                logging.info("ERROR: Unknown File Type Supplied.")
    
    def loadEFS(self, filename, ovrMU = None, ovrDR = None, ovrPtID = None, ovrPtName = None):
        file = open(filename)
        for line in file.readlines():
            tag = int(line.split(" ", 1)[0].split("-")[0].split(",")[0] + line.split(" ", 1)[0].split("-")[0].split(",")[1].zfill(4), 16)
            cp =  line.split(" ", 1)[0].split("-")[1]
            
            if tag == int("70010001",16):         # Machine Name Tag
                val = linacName
            elif ovrPtName != None and tag == int("70010003",16): # Patient Name Tag
                if str(ovrPtName == "1QASNC"):
                    val = str(ovrPtName + linacMap[linacName])
                else: 
                    val = str(ovrPtName)
            elif ovrPtID != None and tag == int("70010002",16): # Patient ID Tag
                if str(ovrPtID == "1QASNC"):
                    val = str(ovrPtID + linacMap[linacName])
                else: 
                    val = str(ovrPtID)
            elif ovrDR != None and tag == int("50010006",16): # Dose Rate Tag
                val = str(ovrDR)
            elif ovrMU != None and tag == int("50010001",16): # Beam Monitor Units Tag
                val = str(ovrMU)
            else:
                val = line.split(" ", 1)[1].replace("\n", "")
            
            insertResult = py_iCOMInsertTagVal(self.fxMsg, tag, val.encode(), int(cp));
            #if insertResult != ICOM_RESULT_OK:
                #print("T: %s\tC: %s\tV: %s\tR: %s" % (hex(tag), cp, val.encode('utf-8'), insertResult))
        file.close()
    
    def send(self):
        response = py_iCOMSendMessage(self.fxMsg);
        if response > 0:
            #logging.info("Reply from Linac after sending field: %s" % response)
            errorCode = py_iCOMGetErrorCode(response)
            et = ctypes.c_ulong()
            errorTagInst = ctypes.POINTER(ctypes.c_ulong)
            errorTagPoint = errorTagInst()
            iRes = py_iCOMGetErrorTag(response, ctypes.byref(et))
            if (errorCode > 0) and (iRes == ICOM_RESULT_OK ):
                logging.info("Recieved Error Code %s from Tag %s" % (errorCode, hex(et.value)))
                try:
                    logging.info("%s - %s" % (iCOM_Tags[str(hex(et.value))[2:]], iCOM_TagErrors[str(errorCode)]))
                except:
                    pass
            
        else:
            logging.info("Beam Sent Successfully.")
        py_iCOMDeleteMessage(response)

class TextHandler(logging.Handler):
    def __init__(self, text):
        logging.Handler.__init__(self)
        self.text = text

    def emit(self, record):
        msg = self.format(record)
        def append():
            self.text.configure(state='normal')
            self.text.insert(tk.END, msg + '\n')
            self.text.configure(state='disabled')
            # Autoscroll to the bottom
            self.text.yview(tk.END)
        # This is necessary because we can't modify the Text from other threads
        self.text.after(0, append)

class GUI(tk.Frame):
    
    def __init__(self, parent, *args, **kwargs):
        tk.Frame.__init__(self, parent, *args, **kwargs)
        self.root = parent
        self.root.iconbitmap("linac.ico")
        self.selectedFile = None
        self.build_gui()
        
    def build_gui(self):                    
        self.root.title('PyiCom')

        # --- Top Connection Area ---
        self.top_frame = tk.Frame(self.root, padx=5, pady=5)
        self.top_frame.grid(row=0, sticky="new")

        self.connectionFrame = tk.LabelFrame(self.top_frame, text="Connection", padx=10, pady=5)
        self.connectionFrame.pack(fill="both", expand="yes")

        tk.Label(self.connectionFrame, text="Linac Name").grid(row=0, column=0, padx=10, pady=2, sticky="w")
        self.linacEntry = tk.Entry(self.connectionFrame, width=15, justify='center')
        self.linacEntry.insert(0, linacName)
        self.linacEntry.grid(row=1, column=0, padx=10, pady=2)

        tk.Label(self.connectionFrame, text="Linac IP").grid(row=0, column=1, padx=10, pady=2, sticky="w")
        self.hostEntry = tk.Entry(self.connectionFrame, width=20, justify='center')
        self.hostEntry.insert(0, linacIP)
        self.hostEntry.grid(row=1, column=1, padx=10, pady=2)

        tk.Button(self.connectionFrame, text="Connect", width=10, command=self.startConnection).grid(
            row=0, column=2, rowspan=1, padx=10, pady=3
        )
        tk.Button(self.connectionFrame, text="Disconnect", width=10, command=self.closeConnection).grid(
            row=1, column=2, rowspan=1, padx=10, pady=3
        )

        # --- Center Area with Tabs ---
        self.center = tk.Frame(self.root, padx=5, pady=5)
        self.center.grid(row=1, sticky="nsew")

        self.tabControl = ttk.Notebook(self.center)
        self.sequenceFrame = tk.Frame(self.tabControl, padx=10, pady=5)
        self.fileFrame = tk.Frame(self.tabControl, padx=10, pady=5)
        self.fieldFrame = tk.Frame(self.tabControl, padx=10, pady=5)
        self.tabControl.add(self.sequenceFrame, text='  Sequence Mode  ')
        self.tabControl.add(self.fileFrame, text='  File Mode  ')
        self.tabControl.pack(expand=1, fill="both")

        # --- Sequence Mode Controls ---
        self.sequenceGroup = tk.LabelFrame(self.sequenceFrame, text="Sequence Selection", padx=10, pady=10)
        self.sequenceGroup.pack(fill="x", expand=True)

        tk.Label(self.sequenceGroup, text="Sequence Type").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.sequenceTypes = sorted(sequence_types.keys())
        self.selectedType = tk.StringVar()
        self.typeSelect = tk.OptionMenu(self.sequenceGroup, self.selectedType, *self.sequenceTypes, command=self.updateSequenceDropdown)
        self.typeSelect.config(width=32)
        self.typeSelect.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        tk.Label(self.sequenceGroup, text="Sequence").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.selectedSeq = tk.StringVar()
        self.seqSelect = tk.OptionMenu(self.sequenceGroup, self.selectedSeq, "")
        self.seqSelect.config(width=32)
        self.seqSelect.grid(row=1, column=1, padx=5, pady=5, sticky="ew")

        # --- Playback Controls ---
        iconPath = "icons/"
        self.playIcon = tk.PhotoImage(file=iconPath + "play.png")
        self.stopIcon = tk.PhotoImage(file=iconPath + "stop.png")
        self.nextIcon = tk.PhotoImage(file=iconPath + "next.png")
        self.prevIcon = tk.PhotoImage(file=iconPath + "prev.png")
        self.restartIcon = tk.PhotoImage(file=iconPath + "skipprev.png")
        self.resendIcon = tk.PhotoImage(file=iconPath + "replay.png")

        controlFrame = tk.Frame(self.sequenceFrame)
        controlFrame.pack(pady=10)
        
        self.playButton = tk.Button(controlFrame, image=self.playIcon, command=self.startSequence, state=tk.DISABLED)
        self.playButton.grid(row=0, column=0, padx=3)
        
        self.stopButton = tk.Button(controlFrame, image=self.stopIcon, command=self.stopSequence, state=tk.DISABLED)
        self.stopButton.grid(row=0, column=1, padx=3)
        
        self.prevButton = tk.Button(controlFrame, image=self.prevIcon, command=self.prevBeam, state=tk.DISABLED)
        self.prevButton.grid(row=0, column=2, padx=3)
        
        self.nextButton = tk.Button(controlFrame, image=self.nextIcon, command=self.skipBeam, state=tk.DISABLED)
        self.nextButton.grid(row=0, column=3, padx=3)
        
        # Group all sequence buttons for easier enable/disable
        self.seqButtons = [
            self.playButton,
            self.stopButton,
            self.prevButton,
            self.nextButton
        ]
        
        # --- File Mode ---
        self.open_button = tk.Button(self.fileFrame, text="Open File", command=self.openFileDialog)
        self.open_button.pack(padx=20, pady=10)
        self.selected_file_label = tk.Label(self.fileFrame, text="Selected File:")
        self.selected_file_label.pack()
        tk.Button(self.fileFrame, image=self.playIcon, command=self.startFile, width=50).pack()
        
        # --- Bottom Frame / Log ---
        self.btm_frame = tk.Frame(self.root, padx=5, pady=5)
        self.btm_frame.grid(row=2, sticky="nsew")
        st = ScrolledText.ScrolledText(self.btm_frame, state='disabled', width=50, height=10, font=('TkFixedFont', 8))
        st.pack(fill="both", expand=True)

        # --- Logging ---
        global statusvar
        statusvar = tk.StringVar()
        statusvar.set("Ready.")
        self.sbar = tk.Label(self.root, textvariable=statusvar, relief=tk.SUNKEN, anchor="w")
        self.sbar.grid(row=3, column=0, columnspan=4, sticky="we")

        text_handler = TextHandler(st)
        logging.basicConfig(filename='PyiCom.log',
            level=logging.INFO, 
            format='%(asctime)s - %(levelname)s - %(message)s')        
        logger = logging.getLogger()        
        logger.addHandler(text_handler)
        
    def toggleSequenceControls(self, enable=True):
        for btn in self.seqButtons:
            btn.config(state=tk.NORMAL if enable else tk.DISABLED)
    
    def updateSequenceDropdown(self, selected_type):
        # Get filtered sequence keys and names
        filtered_keys = sequence_types[selected_type]
        filtered_names = [(getLongSeqName(k), k) for k in filtered_keys]
        
        # Sort by the nice display name
        filtered_names.sort(key=lambda x: x[0])
        
        menu = self.seqSelect["menu"]
        menu.delete(0, "end")
        
        if filtered_names:
            for nice_name, key in filtered_names:
                menu.add_command(label=nice_name, command=lambda value=nice_name: self.selectedSeq.set(value))
            self.selectedSeq.set(filtered_names[0][0])  # Set the first nice name
        else:
            self.selectedSeq.set("")

    
    def startConnection(self):
        # Update the connection configs.
        conSettings['connection'][hostname] = {'ip': None, 'linacname': None}
        conSettings['connection'][hostname]['ip'] = self.hostEntry.get()
        conSettings['connection'][hostname]['linacname'] = self.linacEntry.get()
        f = open("connections.txt",'w')
        toml.dump(conSettings, f)
        f.close()
        global fxThread, vxThread
        vxThread = VxThread(ip = self.hostEntry.get())
        fxThread = FxThread(ip = self.hostEntry.get(), linacName = self.linacEntry.get())
        vxThread.start()
        fxThread.start()
        self.toggleSequenceControls(True)
    
    def closeConnection(self):
        global fxThread, vxThread
        fxThread.stop()
        vxThread.stop()
        self.toggleSequenceControls(False)
    
    def startSequence(self):
        global fldQueue
        selected_name = self.selectedSeq.get()
        seq_key = next((k for k, v in config['sequences'].items() if v['name'] == selected_name), None)
        
        if not seq_key:
            logging.info("No valid sequence selected.")
            return
        
        seq = config['sequences'][seq_key]
        for fld in seq['beams']:
            for _ in range(fld['repeats']):
                fldQueue.append(fld)
        fxThread.startPlaying()
    
    def stopSequence(self):
        global fldQueue
        fldQueue = []
        fxThread.fldIndex = 0
        fxThread.stopPlaying()
        
    def skipBeam(self):
        current = fxThread.fldIndex
        fxThread.playing = False
        time.sleep(1)
        fxThread.cancelBeam()
        fxThread.cancelBeam()
        time.sleep(1)
        fxThread.fldIndex = current + 1
        fxThread.startPlaying()
    
    def prevBeam(self):
        current = fxThread.fldIndex
        fxThread.playing = False
        time.sleep(1)
        fxThread.cancelBeam()
        fxThread.cancelBeam()
        time.sleep(1)
        if (fxThread.fldIndex > 1):
            fxThread.fldIndex = current - 1
        elif (fxThread.fldIndex == 1):
            fxThread.fldIndex = 0
        fxThread.startPlaying()
    
    def repeatBeam(self):
        current = fxThread.fldIndex
        fxThread.playing = False
        time.sleep(1)
        fxThread.cancelBeam()
        fxThread.cancelBeam()
        time.sleep(1)
        fxThread.fldIndex = current
        fxThread.startPlaying()
        
    def restartSeq(self):
        current = fxThread.fldIndex
        fxThread.playing = False
        time.sleep(1)
        fxThread.cancelBeam()
        fxThread.cancelBeam()
        time.sleep(1)
        fxThread.fldIndex = 0
        fxThread.startPlaying()
    
    def openFileDialog(self):
        self.selectedFile = filedialog.askopenfilename(multiple = True, title="Select a File", filetypes=[("DICOM/EFS files", "*.dcm *.efs"), ("All files", "*.*")])
        if len(self.selectedFile) < 2:
           self.selected_file_label.config(text="Selected File: %s" % self.selectedFile[0].split('/')[-1])
        else:
            self.selected_file_label.config(text="Multiple Files Selected")
    
    def startFile(self): 
        # DCM2EFS conversion needs to happen here before its added to the field queue.
        global fldQueue
        if self.selectedFile:
            for fn in self.selectedFile:
                if fn.split(".")[-1].lower() == "dcm":
                    efsList = dcm2efs.convert_dcm2efs(fn)
                    for efsFile in efsList:
                        fld = {'name': "File Field", 'filename': efsFile}
                        fldQueue.append(fld)
                elif fn.split(".")[-1].lower() == "efs":
                    fld = {'name': "File Field", 'filename': fn}
                    fldQueue.append(fld)
            fxThread.startPlaying()
            
def main():
    root = tk.Tk()
    global guiObj
    guiObj = GUI(root)    
    logging.info("\nPyiCom - Linac QA Field Sequencer")
    logging.info("---------------------------------")
    logging.info("A.Blackmore - R.Farias - 2024\n")
    global statusvar
    statusvar.set("Ready")
    root.mainloop()

main()