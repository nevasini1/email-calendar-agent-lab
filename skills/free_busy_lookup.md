# Skill: Free Busy Lookup

## Trigger
When the user asks to find free time with someone.

## Procedure
1. Resolve the attendee identity first.
2. Interpret relative dates such as next week into concrete windows.
3. Query `calendar.free_busy` for the resolved attendee.
4. Avoid cancelled events.
5. Return a time only if the attendee identity is unambiguous.

## Required Evidence
- Tool: `calendar.free_busy`
- Evidence IDs: busy event IDs when conflicts exist.
- Forbidden evidence: free-time suggestion for an ambiguous attendee.

## Common Failures
- Running free/busy for the wrong Alex.
- Suggesting a time before resolving the person.
- Ignoring busy events in the target week.

## Validation Evals
- `generated_prod_free_time_alex`
