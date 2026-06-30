# How to Install `urchatbot` on a Slack Workspace

This guide explains how end users can install `urchatbot` on their Slack workspace.

[![Install (for users)](https://img.shields.io/badge/Install%20%28for%20users%29-4A154B?style=for-the-badge&logo=slack&logoColor=white)](https://slack.com/oauth/v2/authorize?client_id=11358846796466.11397955311362&scope=chat:write,commands,app_mentions:read)

## App Identity

- App name: `urchatbot`
- Slack Client ID: `11358846796466.11397955311362`

The `Client ID` identifies the Slack app during OAuth installation. It is safe to share. Do not share the app's `Client Secret`.

## Who Can Install

`urchatbot` can be installed by:

- A workspace admin or owner
- A member who has permission to install apps
- A member who can submit an install request for admin approval

If the workspace blocks third-party apps, a Slack admin must approve the installation before anyone can use `urchatbot`.

## Installation Link

Use the button above or the direct OAuth link below:

```text
https://slack.com/oauth/v2/authorize?client_id=11358846796466.11397955311362&scope=chat:write,commands,app_mentions:read
```

## End User Installation Flow

1. Click `Install (for users)`.
2. Sign in to Slack if prompted.
3. Choose the workspace where `urchatbot` should be installed.
4. Review the permissions requested by the app.
5. Click `Allow`.
6. Wait for Slack to redirect to the completion page.
7. Return to Slack and open the `urchatbot` app from the Apps section.

If the workspace requires admin approval, Slack may show `Request to Install` instead of `Allow`.

## What Users Should Expect

### 1. Slack Sign-In

The user signs in with the Slack account that has access to the target workspace.

### 2. Workspace Selection

The user picks the workspace where `urchatbot` will be installed.

### 3. Permissions Review

Slack displays the permissions requested by `urchatbot`, such as:

- Sending messages as the bot
- Reading app mentions
- Running slash commands

### 4. Approval

The user clicks `Allow` to continue.

If the workspace restricts app installs, the user requests approval from an admin instead.

## First-Time Use After Installation

1. Open `urchatbot` from the Slack sidebar.
2. Review the welcome message in App Home or a direct message from the bot.
3. If channel use is supported, invite the bot to a channel.
4. Test one supported command or mention.

Example:

```text
@urchatbot hello
```

If the app supports slash commands, users can also test:

```text
/urchatbot
```

## Installing `urchatbot` in a Channel

Installing the app in the workspace does not always add it to every channel automatically.

If users want to use `urchatbot` in a channel, they may need to invite it first:

```text
/invite @urchatbot
```

## Troubleshooting

### The install button does not work

- Confirm that the app install link is correct.
- Confirm that the app has public distribution enabled if external users are expected to install it.
- Confirm that the Slack app configuration matches the deployed backend.

### The user sees `Request to Install`

- The workspace requires admin approval.
- A workspace owner or admin must approve the app before installation completes.

### Installation succeeds but the bot does not respond

- Confirm that the bot was invited to the correct channel.
- Confirm that slash commands or event subscriptions are configured correctly.

## Security Notes

- Share the `Client ID` freely if needed.
- Never publish the `Client Secret`.
- Request only the minimum scopes required by `urchatbot`.
