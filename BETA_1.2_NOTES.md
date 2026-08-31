# X BOT V2 Beta 1.2

## Job Inactivity System

- Warns a member after 7 days without a successful `/work` shift.
- Removes the member's X BOT job after 14 inactive days.
- Runs automatically once per hour while X BOT is online.
- Sends warnings and dismissals to the configured Discord Job Log Channel.
- Records every warning and dismissal in Dashboard Economy Logs.
- A successful `/work` clears the warning and resets inactivity timing.
- Each inactivity warning is sent only once.
- Discord department/staff roles are never removed automatically.

## Configuration

- Dashboard > Jobs > Job Inactivity Rules controls the feature, warning days, firing days, and channel.
- `/job_log_channel channel:#channel` lets an Administrator set the Discord channel without opening Dashboard.
- Default rules: warning at 7 days, firing at 14 days.

The pre-upgrade database is stored at `backups/xwar-before-beta-1.2.db`.
