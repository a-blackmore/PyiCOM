# PyiCOM
Python implementation of the Elekta iCOM Library

# Installation
Download the repository, and add the required dependencies to the main folder:
* From an Elekta Linac, locate iCOMClient.dll - This should be present on the iCom CAT CD provided with the machine at install. It should also be present on many of the adjoining systems like XVI and iView. Copy it into to the main directory - unfortunately these files aren’t mine to distribute freely.
* Install a portable version of Python 3.4 in the python_3.4 folder. Unfortunately, this legacy version is required as it is the last to support the 32 bit DLL that we need to use. (P.S. Elekta - If you want to provide me a more recent 64bit DLL that would be fab). Ensure your python installation has the required pre-requisite libraries as specified in requirements.txt. Alternatively, you can unzip the version I've provided.

# Configuration
Using the config.txt file, you can specify the sequences you wish to deliver. There are a few examples provided to demonstrate the format. I recommend the use of the iCom CAT tool from Elekta to help specify more EFS files, or you can edit them with a text editor. You can override the MU and Dose Rate and add Move Only segments to streamline your QA. 

# Running PyiCOM
* Put your Linac into Clinical Receive Prescription Mode, and Close Mosaiq. 
* Ideally place PyiCOM on a network drive that is accessible from within the Linac's network - then run it from the CCP Management PC, iView, or XVI. It doesn't require any installation or leave a footprint on these devices.
* Configure PyiCOM with your Linac's name and IP address (generally from within the network this is the last four digits of the linac's serial number, and 192.168.30.2)
* Press "Connect", then select a sequence and press "Play" - The linac should mode up the field and be ready to deliver.
