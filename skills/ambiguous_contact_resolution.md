# Skill: Ambiguous Contact Resolution

## Trigger
When a query mentions a first name or alias that matches multiple contacts.

## Procedure
1. Resolve the contact name against known contacts and aliases.
2. If multiple people match, ask a clarification question.
3. Include the possible full names in the clarification.
4. Do not choose a person silently.
5. Only run person-specific free/busy after ambiguity is resolved or when showing candidate options.

## Required Evidence
- Tool: contact resolution and, when needed, `calendar.free_busy`.
- Evidence IDs: matching contacts or related calendar events.
- Forbidden evidence: single-person answer when multiple contacts match.

## Common Failures
- Choosing Alex Rivera when the user only said Alex.
- Confusing Sarah and Sara.
- Treating assistants or senders as the actual meeting participant.

## Validation Evals
- `generated_prod_free_time_alex`
- `heldout_sarah_not_sara`
