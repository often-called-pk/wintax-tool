# WinTAX ECU Downloader: user guide

This app downloads the recorded data from your Marelli F4 ECU to your PC. You do
not need any programming knowledge. You set two things, you click two buttons,
and you send one file back.

## Before you start

1. Use a Windows PC.
2. Connect the PC to the car or the ECU with an Ethernet cable. A direct cable or
   a small switch is fine.
3. Turn on the ECU.
4. Close the WinTAX program if it is open. Two programs cannot use the ECU at the
   same time.

## Step 1: Open the app

Double-click `WinTAX-ECU-Downloader.exe`. The window opens.

## Step 2: Set the ECU IP address

The ECU IP box shows `192.168.111.254`. This is the normal address. Change it only
if your team told you a different address.

## Step 3: Choose the output folder

Click Browse next to Output folder. Pick an empty folder, for example a new folder
on your Desktop. The app writes the data, the logs, and the result file here.

## Step 4: Test the connection

Click Test connection. The app looks for the ECU.

- The app shows the ECU name and state. The connection works. Go to Step 5.
- The app shows "No ECU found". Check the cable, the ECU power, and the IP
  address. Close WinTAX. Then click Test connection again.

## Step 5: Download the data

1. Click Download.
2. Read the message. The download arms the ECU and stops its logger while it runs.
   Click Yes to continue.
3. Wait. The progress bar moves and the log shows the data rate. Do not unplug the
   cable.
4. The app shows "done" when the download finishes.

## Step 6 (optional): Decode the data

The Decode button turns a stored file into tables (CSV) and a MATLAB file. Use it
only if your team asked for it.

1. Click Browse next to Input.
2. Pick a `cableData.ztx` file, or a folder that holds lap folders.
3. Click Decode.

## Step 7: Send the result back

1. Click Package to send. The app makes one zip file in your output folder.
2. The app opens the folder and shows the zip name.
3. Send that one zip file to the person who gave you this app. Use email or a file
   share.

The zip holds the downloaded data and the logs. The app sends nothing on its own.
You choose when to send the zip.

## Where the logs are

The app writes logs in the `logs` folder inside your output folder. Each run has a
`.log` file and a `run_*.json` file. These files help the team find any problem.
The Package to send step already includes them in the zip.

## Common problems

- No ECU found. Check the cable. Check the ECU power. Check the IP address. Close
  WinTAX.
- The download stops early or shows an error. Click Download again. Keep the log
  file and send it with the zip.
- The app will not open. Right-click the exe and select Run. Allow it if Windows
  asks.
