# Terraform

All AWS resources for the project come from the stacks in this folder (`CLAUDE.md`,
Infrastructure). Account conventions such as region, profile and tags are in
`docs/aws-account.md`.

## Stacks and state

Each stack is a folder with its own state, kept in one S3 bucket,
`btp-tfstate-<account-id>-eu-north-1`. The bucket is versioned, encrypted with SSE-S3,
blocks public access, refuses non-TLS requests and keeps old state versions for 90 days.
Locking uses S3's native lock file (`use_lockfile = true`, Terraform 1.10 or newer), so there
is no DynamoDB table.

| Stack | Folder | State key | Status |
| --- | --- | --- | --- |
| bootstrap | `bootstrap/` | `bootstrap/terraform.tfstate` | The state bucket itself (#13) |
| release | `release/` | `release/terraform.tfstate` | M2 |
| poc | `aws-microservices/`, `eks/` | `poc/terraform.tfstate` | Alternatives, never needed by the release path |

## Who applies

Only the maintainer runs `terraform apply` (and `destroy`, `import`, `state`), after reading
the plan. Claude Code writes the code and runs `make tf-validate`, which needs no credentials.
The plan output goes in the PR, with the account ID redacted.

```bash
make tf-validate STACK=bootstrap   # init -backend=false, fmt -check, validate
make tf-plan STACK=bootstrap       # init, fmt -check, validate, plan -out=plan.tfplan
```

## Credentials

Sign in with `aws login --profile btp`; the providers use the `btp` profile. If Terraform
reports "no valid credential sources", the provider doesn't read `aws login` sessions yet.
Export short-lived credentials into the shell and plan again:

```bash
eval "$(aws configure export-credentials --profile btp --format env)"
```

## Bootstrap procedure (once)

The bootstrap stack creates the bucket its own state will live in, so its first apply uses
local state and then migrates it.

1. `aws login --profile btp`
2. `make tf-plan STACK=bootstrap`, then read the plan: 6 resources, all S3 (bucket,
   versioning, encryption, public access block, bucket policy, lifecycle). Cost: cents a month.
3. `terraform -chdir=deploy/terraform/bootstrap apply plan.tfplan`
4. Switch on the S3 backend and move the state into the bucket:

   ```bash
   git mv deploy/terraform/bootstrap/backend.tf.example deploy/terraform/bootstrap/backend.tf
   terraform -chdir=deploy/terraform/bootstrap init -migrate-state \
     -backend-config="bucket=$(terraform -chdir=deploy/terraform/bootstrap output -raw state_bucket_name)"
   ```

   Answer `yes` to copy the existing state.
5. `terraform -chdir=deploy/terraform/bootstrap state list` lists the 6 resources. Then delete
   the local `terraform.tfstate` and `terraform.tfstate.backup` in `deploy/terraform/bootstrap/`
   and commit `backend.tf`.

The bucket has `prevent_destroy`. Removing it takes a code change and a reviewed PR.

## Adding a stack

Give the stack a `backend.tf` with its own key. Leave `bucket` out: the name contains the
account ID, which stays out of the repository (partial backend configuration).

```hcl
terraform {
  backend "s3" {
    key          = "release/terraform.tfstate"
    region       = "eu-north-1"
    profile      = "btp"
    encrypt      = true
    use_lockfile = true
  }
}
```

On a machine's first init of the stack, pass the bucket name once. Terraform remembers it in
`.terraform/` after that:

```bash
make tf-plan STACK=release TF_STATE_BUCKET=btp-tfstate-<account-id>-eu-north-1
```

Also: pin the same `required_version` and provider major as `bootstrap/versions.tf`, use
`default_tags` with `Project`, `Stack` and `ManagedBy`, and commit `.terraform.lock.hcl`
generated for every platform:

```bash
terraform -chdir=deploy/terraform/<stack> providers lock \
  -platform=darwin_arm64 -platform=linux_amd64 -platform=linux_arm64
```

Never commit state, plan files or `*.tfvars` (`.gitignore` excludes them).
