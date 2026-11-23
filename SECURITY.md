# Security Policy

## Reporting a vulnerability

Do not open a public issue, pull request or discussion for a security problem.

Report it to Tyk's security team by email at **security@tyk.io**, as described in Tyk's
[coordinated vulnerability disclosure policy](https://tyk.io/responsible-disclosure/). That
policy covers Tyk products and services; it also sets out what to include, what Tyk does next,
and the rules for testing (use only test accounts, and do not share the problem with others until
it is fixed).

Please include:

- what is affected: a skill and file in this repository, or a Tyk product and its version;
- what the problem is and how to reproduce it;
- the impact you think it could have.

## What belongs here

Report through the address above, not as an issue, anything such as:

- a skill, script or generated demo bundle that exposes a credential, licence or private key, or
  publishes a service beyond the local machine without saying so;
- a script in this repository that runs or downloads something it should not;
- a skill that tells an agent to do something unsafe on the user's machine or stack.

A wrong fact, a skill that does not trigger, or a recipe that fails is not a security problem.
Open an issue for those with the forms in this repository.

## Supported versions

Fixes go into the latest release of this plugin. Update to it with the steps in the README.
