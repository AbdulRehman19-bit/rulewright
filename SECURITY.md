# Security

Report vulnerabilities privately through GitHub: open
[a security advisory](https://github.com/AbdulRehman19-bit/rulesmith/security/advisories/new)
on this repository. Please do not file a public issue for a vulnerability.

## Threat model

rulesmith writes text that coding agents will obey, so all mined text (commit messages, diffs,
review comments, session logs) is untrusted. Mined text is only ever passed to an LLM inside a
delimited data block, proposed rules pass a risk scanner, each rule must cite 3+ verified
commits, secrets are redacted before any API call, and no file is changed without `apply`.
