# Privacy and Release Transformations

This public package is deidentified outcome microdata, not a claim that
behavioral records can never be unique. The release minimizes linkage risk
while retaining the within-participant structure required for paired analyses.

## Removed before release

- names and contact information;
- raw IP addresses and source-platform identifiers;
- exact submission time and response duration;
- original row order and source metadata;
- free-text reasons and feedback;
- exact age;
- participant-level city and province;
- participant-level gender;
- persona-match and timing-based QC flags;
- the source-to-release participant-ID mapping.

## Retained

- a newly randomized ID used only to pair three method judgments;
- assigned role-play persona;
- four-level age band;
- binary authorization;
- 0-5 satisfaction outcome;

The public participant table has a minimum cell size of eight for the released
`persona × age_band` combinations. Fine-grained geographic distributions,
including singleton cities and provinces, are not included. No precomputed
analysis tables are included.

The original transfer ZIP must not be committed, attached to a GitHub release,
or copied into an anonymous repository. Deleting it in a later commit would
not remove it from Git history.
