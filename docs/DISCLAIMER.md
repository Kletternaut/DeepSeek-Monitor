# Tariff Accuracy Disclaimer and Mandatory User Verification

DeepSeek-Monitor is an independent planning tool, not an official DeepSeek billing service. Its tariff monitor is intended to help users understand and plan around published pricing periods. It is **not an authoritative source for determining the charges DeepSeek will apply**.

## Why tariff times can be difficult to calculate correctly

The monitor must combine multiple rules and time conversions correctly:

- DeepSeek's current Peak and Off-Peak windows and their exact start and end instants.
- Weekend boundaries as defined by the applicable billing rules.
- The Chinese public-holiday calendar, which users outside China may not be familiar with and which must be updated when the official holiday schedule is announced.
- Conversion of those rules to the user's selected local timezone, including changes between standard time and daylight-saving time, such as the seasonal clock changes in Europe.
- Correct system time, timezone-database data, application logic and configuration.
- Changes DeepSeek may make to prices, multipliers or billing rules.

A display can look plausible and still be wrong because of outdated holiday data, a rule change, timezone conversion, daylight-saving-time handling, incorrect configuration or a software defect. The holiday dates and tariff rules are maintained in code and may not automatically reflect later changes.

Check DeepSeek's current official [pricing documentation](https://api-docs.deepseek.com/quick_start/pricing/) for the applicable rules. DeepSeek may update its prices or billing schedule.

## Mandatory self-check before cost-sensitive use

**Before relying on the monitor for paid API work, you must independently verify that the initially displayed tariff zones and their start and end times are correct. Do not treat the display as verified until you have completed this check.**

At a minimum:

1. Confirm that the monitor is set to the correct local timezone.
2. Compare the displayed Peak and Off-Peak periods and the relevant weekend and Chinese public-holiday periods with DeepSeek's current official pricing rules.
3. Verify the corresponding start and end times in your local timezone, paying particular attention to the European change between standard time and daylight-saving time, where applicable.
4. Check the upcoming transitions shown by the countdown and timeline. Do not rely on the monitor for cost-sensitive scheduling if you cannot verify the displayed times independently.

Repeat the check after installing an application update, changing the selected timezone or relevant system timezone data, each daylight-saving-time transition, updating the Chinese public-holiday calendar, or any announcement that changes DeepSeek's prices or billing rules. Re-check the official schedule periodically.

If the displayed times do not match the official rules—or you cannot establish that they match—**do not rely on the monitor to decide when to run token-intensive work**. Use DeepSeek's current official pricing information and account usage/billing records instead. DeepSeek's billing records, not this monitor, determine the charges actually applied.

## No guarantee of tariff accuracy

The tariff zone, multiplier, countdown, timeline and cost estimates are provided for informational and planning purposes. The project maintainers do not guarantee that these values will always be accurate, complete, current or uninterrupted. Incorrect information may lead users to schedule work in a higher-cost period than expected and incur increased API costs.

The software is distributed under the terms of the [MIT License](../LICENSE), which contains its own warranty and liability provisions. This tariff-specific notice highlights the risks and the required user verification; it does not purport to exclude or limit liability where applicable law does not permit that exclusion or limitation.
