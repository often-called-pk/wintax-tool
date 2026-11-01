# WinTAX ECU Downloader: user guide

This app downloads the recorded data from your Marelli F4 ECU to your PC. You do
not need any programming knowledge. You set a few things, you click two buttons,
and you send one file back.

## Before you start

1. Use a Windows PC.
2. Connect the PC to the car or the ECU with an Ethernet cable. A direct cable or
   a small switch is fine.
3. Set the PC wired network adapter to the ECU network. Use a fixed address such
   as `192.168.111.10` with mask `255.255.255.0`. The ECU is at
   `192.168.111.254`. Ask your team if you do not know how to set the address.
4. Turn on the ECU.
5. Close the WinTAX program. Two programs cannot use the ECU at the same time.

## Step 1: Open the app

Double-click `WinTAX-ECU-Downloader.exe`. Windows can show a blue box, "Windows
protected your PC". Click "More info", then "Run anyway". The app is new and
unsigned, so Windows warns one time. The window opens.

## Step 2: Set the ECU IP address

The ECU IP box shows `192.168.111.254`. This is the normal address. Change it only
if your team told you a different address.

## Step 3: Choose the output folder

Click Browse next to Output folder. Pick an empty folder, for example a new folder
on your Desktop. The app writes the data, the logs, and the result file here. Use
a separate output folder for each car or each session. This keeps the results
apart.

## Step 4: Test the connection

Click Test connection. The app looks for the ECU. If Windows asks to allow the app
on the network, allow it on private networks.

- The app shows the ECU name and state. The connection works. Go to Step 5.
- The app shows that no ECU was found. Check the cable, the ECU power, the PC
  address, and the ECU IP box. Close WinTAX. Then click Test connection again.

## Step 5: Download the data

1. Click Download.
2. Read the message. The download arms the ECU and stops its logger while it runs.
   Click Yes to continue.
3. Wait. The progress bar moves and the log shows the data rate. Do not unplug the
   cable.
4. The app shows the result when the download finishes.

## Step 6 (optional): Decode the data

The Decode button turns a stored file into tables (CSV) and a MATLAB file. Use it
only if your team asked for it. Leave the Input box empty unless you decode a
file.

1. Click Browse next to Input.
2. Pick a `cableData.ztx` file, or a folder that holds lap folders.
3. Click Decode.

## Step 7: Send the result back

1. Click Package to send. The app makes one zip file in your output folder.
2. The app opens the folder and shows the zip name.
3. Send that one zip file to the person who gave you this app.

The zip can be large, about 100 MB or more. Email can reject a file this big. Use
a file share or a USB drive if the zip is too big for email. The app sends nothing
on its own. You choose when to send the zip.

## Where the logs are

The app writes logs in the `logs` folder inside your output folder. Each run has a
`.log` file and a `run_*.json` file. These files help the team find any problem.
The Package to send step already puts them in the zip. The logs can include your
Windows user name inside file paths. This is normal.

## Common problems

- No ECU found. Check the cable. Check the ECU power. Check the PC address and the
  ECU IP box. Close WinTAX.
- The download stops early or shows an error. Read the message for the cause. Try
  Download again. Keep the log file and send it with the zip.
- The app will not open. If Windows shows the blue "Windows protected your PC"
  box, click "More info", then "Run anyway".
