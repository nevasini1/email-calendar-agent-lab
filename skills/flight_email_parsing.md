# Skill: Flight Email Parsing

## Trigger
When the user asks about recent flights, destinations, arrival cities, or arrival times.

## Procedure
1. Search Gmail for flight-related receipts or itineraries.
2. Sort by sent time and use the newest relevant itinerary.
3. Parse destination from `to <airport>` or `arrives <airport>`.
4. Preserve the timezone exactly as written in the source email.
5. Answer only with values supported by the flight email.

## Required Evidence
- Tool: `gmail.search_emails`
- Evidence IDs: flight email IDs.
- Forbidden evidence: origin airport when destination is requested.

## Common Failures
- Returning the origin instead of the destination.
- Converting PT arrival times into ET without being asked.
- Using older flight emails.

## Validation Evals
- `stable_last_flight_destination`
- `generated_prod_flight_arrival_timezone`
- `heldout_timezone_flight`
