# PIA Windows companion

A small PowerShell script that shows PIA's notifications as ordinary Windows notifications. Clicking one opens
the item in PIA. It polls the server once a minute and does nothing else: no model, no database, no heavy work.

## Set up

1. In PIA, open **Notifications**, scroll to **Devices**, name this computer and choose **Create a device
   token**. Copy the token (it is shown once). From a terminal, `pia add-device "My laptop"` does the same.
2. In PowerShell, from this folder:

   ```powershell
   .\install.ps1 -Url http://localhost:8765 -Token pia_dev_your_token_here
   ```

   Use your server's `https://` address instead of `localhost` once PIA runs in the cloud. If PowerShell refuses
   to run the script, run it as `powershell -ExecutionPolicy Bypass -File .\install.ps1 -Url ... -Token ...`.
3. To see a sample notification: `.\pia-companion.ps1 -Test`.

`install.ps1` stores the address and the token (encrypted for your Windows account) in
`%APPDATA%\PIA\companion.json`, registers "PIA" as a notification sender for your user, and adds a Startup
shortcut so the companion starts hidden when you sign in. Nothing needs administrator rights.

## What decides which notifications appear

The server does. In PIA's Notifications page you set the thresholds for Interesting, Important and Critical,
daily caps, the minimum level shown on the desktop, and quiet hours (during which only Critical ones are
delivered). The companion shows whatever the server hands it, once per device.

A laptop that has been off does not replay old notifications: the server only hands over ones from the last 24
hours that you have not already seen in the app.

## Remove

```powershell
.\install.ps1 -Uninstall
```

Then revoke the device in PIA (Notifications > Devices) so its token stops working.

## Troubleshooting

- **Nothing appears.** Check Windows Settings > System > Notifications: notifications must be on, and Focus /
  Do not disturb off. Run `.\pia-companion.ps1 -Test`.
- **"The server could not be reached".** Check the address, that PIA is running, and that the token was copied
  whole. `.\pia-companion.ps1 -Once` prints the error.
- **Toasts are labelled "Windows PowerShell".** `install.ps1` has not been run for this user; run it.
