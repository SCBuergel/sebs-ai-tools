# Finding severity

Each finding is rated for the flaw it describes, not for the size of its fix. The
rating comes from two questions: who must act for the flaw to matter, and what
they then gain or what the user then loses. Replace the right column with the
trust boundaries and failure costs of the reviewed project; keep the five names,
the dashboards and the dev queue sort by them.

| Rating | Meaning |
|---|---|
| Critical | An attacker or a plain failure crosses the project's main trust boundary, with no extra precondition: code runs where it must not, data leaves where it must not, or a core guarantee silently fails. |
| High | A high-value part is compromised, or a control silently fails, without any user mistake. |
| Medium | A documented control is defeated, but only with one extra precondition: a malicious change reaching the reviewed tree, or a supported but unusual configuration. |
| Low | Defence in depth. Exploiting it needs the trusted user to make a mistake, or a second control to fail first. |
| Info | Docs or code are wrong or stale with no effect on the project's guarantees. |

Cosmetic changes (wording, formatting, nits) are not rated and are not findings.
