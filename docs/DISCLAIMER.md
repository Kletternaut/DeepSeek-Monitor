# Tariff Accuracy, Verification and Liability Disclaimer

DeepSeek-Monitor is an independent, community-maintained tool. It is not an official DeepSeek billing service, and its tariff monitor is intended as an aid for planning API usage—not as an authoritative source for deciding what DeepSeek will charge.

## Why the displayed times must be verified

Correctly displaying a pricing zone in a user's local timezone requires several pieces of information and transformations to remain correct at the same time:

- DeepSeek's current peak and off-peak rules, including the exact start and end instants of each UTC time window.
- Weekday and weekend boundaries and the applicable Chinese public-holiday calendar, which may be unfamiliar to users outside China and must be kept up to date.
- Conversion from the billing schedule to the timezone selected by the user, including changes between standard time and daylight-saving time in regions such as Europe.
- Correct system time, timezone-database rules, application logic and configuration.
- Any changes DeepSeek makes to its prices or billing schedule.

The software's holiday dates and tariff rules are maintained in code and may not reflect a later policy change, newly announced holiday schedule, timezone-database change or an implementation defect. Even if the display appears plausible, its accuracy is not guaranteed.

Refer to [DeepSeek's official pricing documentation](https://api-docs.deepseek.com/quick_start/pricing/) for the current published schedule. The official policy and prices may change.

## Mandatory user verification before relying on the monitor

**Before using the monitor to plan paid API work, the user must independently verify that the initially displayed pricing zones and their start and end times are correct. Do not rely on the monitor until this verification has been completed.**

Compare the displayed zones against DeepSeek's current official pricing documentation, including the transitions relevant to the user's timezone. Check that the Peak/Off-peak boundaries, weekends and applicable Chinese public holidays are shown at the correct local times.

Repeat this verification after application updates, changes to the selected timezone or system timezone data, daylight-saving-time transitions, changes to the annual Chinese public-holiday calendar, or any DeepSeek pricing/billing-policy announcement. Re-check the official schedule periodically. Do not use the monitor as the sole basis for cost-sensitive decisions; DeepSeek's own account usage and billing records are authoritative for the charges actually applied.

## No warranty

The monitor, tariff calculations, countdowns, timeline and cost estimates are provided **“as is” and “as available,” without warranties or guarantees of any kind**, to the maximum extent permitted by applicable law. The maintainers do not warrant that tariff zones, transition times, holiday dates, multipliers, prices or cost estimates will be accurate, complete, current or uninterrupted.

## Limitation of liability

To the maximum extent permitted by applicable law, the project authors and contributors disclaim liability for losses or damages arising from reliance on an incorrect, incomplete, outdated or unavailable tariff display or calculation. This includes unexpected or increased API charges, missed opportunities to use lower-cost periods, and indirect or consequential losses resulting from inaccurate zone boundaries, timezone conversions, daylight-saving-time handling, holiday calendars, configuration, software defects or changes to DeepSeek's billing rules.

Nothing in this disclaimer excludes or limits liability to the extent that such exclusion or limitation is prohibited by applicable law. The enforceability and effect of a liability limitation can depend on the applicable law and the circumstances of use.
