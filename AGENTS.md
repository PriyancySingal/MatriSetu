# MatriSetu — Team Protocol

## Shared Build Datapack

Name: MatriSetu-build

This datapack is the shared development memory for the MatriSetu team.

## 1. Before Every Task

- Recall MatriSetu-build before starting a task.
- Check for relevant architectural decisions, naming conventions,
  implementation details and previously documented issues.
- Follow existing promoted decisions unless the team explicitly
  approves a change.

## 2. After Every Task

- Save a concise summary of what changed and why.
- Record important implementation decisions and technical learnings.
- Do not store secrets, API keys, tokens, patient names, phone
  numbers, medical records or other personal information.

## 3. Promoting Decisions

Promote important decisions that other agents and teammates
must follow, including:

- File and folder naming conventions
- Function and module names
- API contracts and database schemas
- Environment variable names
- Meko integration architecture
- Error handling and fallback rules
- Security and privacy requirements

Before promoting:

- Show the exact proposed text.
- Identify the contributing teammate using an agreed team handle.
- Ask for explicit confirmation.
- Never promote without confirmation.

Promotion is one-way. Verify the content before approval.

## 4. MatriSetu Architecture Rules

- Keep the backend and frontend modular.
- Maintain consistent API contracts.
- Keep configuration and secrets in environment variables.
- Never commit .env files or credentials.
- Use hashed, non-identifying patient identifiers.
- Never store personal healthcare data in MatriSetu-build.

## 5. Meko Integration Rules

- MEKO_ENABLED controls optional Meko integration.
- Meko must never be a single point of failure.
- If Meko is unavailable, MatriSetu must continue operating
  using its existing application logic.
- Meko may raise an urgency level but must never lower
  a clinically determined urgency level.
- Never allow shared memory to override clinical safety rules.

## 6. Team Collaboration

- Check the shared build datapack before making architectural
  changes.
- Avoid duplicating existing implementations.
- Do not overwrite another teammate's work without coordination.
- Record decisions with their contributor attribution.
- Keep the build datapack separate from production data.

## 7. Security

- Never expose API keys, tokens or credentials.
- Never upload identifiable patient information.
- Never commit secrets to GitHub.
- Follow least-privilege access.
- Require human review for clinically significant changes.
