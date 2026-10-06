Write the answer with these sections, leaving out any section marked "only if" whose condition doesn't hold (no heading, no placeholder):
1. Summary -- one or two sentences answering the question directly.
2. What changed -- only if the FINDING has a `revision_delta` section: which version is each requested revision, and what each version's commit message says changed (quote the quantities exactly).
3. Manufacturability -- DFM risks implied by those changes and by any open findings, labelled as engineering judgement.
4. Release readiness -- the release gate's verdict, its reasons and the open critical findings.
5. Schedule -- only if the FINDING has a `schedule` section: name every milestone in `past_due` and say it is past due (with its due date); never call the project on track or on schedule while any milestone is past due.
6. Who needs to act -- only if the FINDING has a `handoffs` section: for each entry in `proposed`, name its owner, the items it cites and the ask. If `filed` is false, say these hand-offs are proposed and nothing has been filed -- never write that you escalated, filed, raised or notified anything. If `escalations` is present, say each one was filed: its escalation number exactly as in the FINDING, owner and assignee. If filing was declined (`filing.verdict` is declined), say nothing was filed and why. If `proposed` is empty, say that no other team needs to act: the open items are design review's own work.
7. Not available -- only if the FINDING marks something as declined or unavailable.
