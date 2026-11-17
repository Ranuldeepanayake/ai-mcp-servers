# AWS MCP Server

A modular, read-only [Model Context Protocol](https://modelcontextprotocol.io/) server for AWS. The first provider is S3 and exposes tools to list objects, inspect metadata, and read bounded object content.

## Scope

- `list_s3_objects`: lists keys and summary metadata, with optional full `HEAD` metadata.
- `list_s3_buckets`: lists buckets visible to the configured AWS identity.
- `read_s3_object`: returns object metadata and UTF-8 content, or base64 for binary content.
- AWS credentials are not stored or accepted by this project. boto3 uses the normal AWS CLI credential provider chain, including `AWS_PROFILE`, `AWS_REGION`/`AWS_DEFAULT_REGION`, environment credentials, `~/.aws/credentials`, and `~/.aws/config`.
- Mounting `~/.aws` only makes profiles available; the selected profile must still be `default` or be named by `AWS_PROFILE`.
- This server does not create, update, delete, copy, or presign S3 objects.

## Requirements

- Python 3.12 (the production image and Dev Container use the same Python minor version).
- An existing AWS CLI/boto3 configuration with permission to call `s3:ListAllMyBuckets`, `s3:ListBucket`, `s3:GetObject`, and `s3:HeadObject` as applicable.
- `uv` is recommended; pip also works.

## Setup and run

```powershell
uv sync --dev
$env:AWS_PROFILE = "your-profile"
$env:AWS_REGION = "us-east-1"
uv run aws-mcp-server
```

The default transport is stdio, which is suitable for MCP desktop clients. For development with the MCP Inspector:

```powershell
uv run mcp dev src/aws_mcp_server/server.py
```

For an MCP client configuration, use the executable command `uv` with arguments `run --directory C:\path\to\ai-mcp-servers aws-mcp-server`. Keep AWS profile and region configuration in the host environment or AWS config files.

## Production Docker image

`Dockerfile` builds the application image independently of VS Code Dev Containers. It runs as a non-root user, listens on Streamable HTTP port `8000`, and expects AWS credentials to be supplied externally through an IAM role, environment variables, or an external AWS configuration mount.

Build and run it directly:

```powershell
docker build -t aws-mcp-server:local .
docker run --rm -p 8000:8000 `
  -e AWS_PROFILE=your-profile `
  -e AWS_REGION=us-east-1 `
  -e AWS_SDK_LOAD_CONFIG=1 `
  -v "${env:USERPROFILE}/.aws:/home/aws-mcp/.aws:ro" `
  aws-mcp-server:local
```

For production, prefer the platform's IAM role or task identity instead of mounting a developer credential directory. The image exposes `8000` and host clients connect to `http://localhost:8000/mcp` when run locally.

## Local Docker Compose workflow

`compose.yaml` is for local development only and is unrelated to `.devcontainer`. It builds the application image, mounts the host AWS CLI configuration read-only, and publishes the MCP endpoint:

```powershell
$env:AWS_PROFILE = "your-profile"
$env:AWS_REGION = "us-east-1"
docker compose up --build
```

Stop it with `docker compose down`. Set `MCP_HOST_PORT` to use a different host port. Set `AWS_CONFIG_DIR` if the AWS configuration is stored somewhere other than `${USERPROFILE}\\.aws`.

## VS Code Dev Container workflow

The Dev Container is an interactive VS Code development environment. It is separate from the local Compose workflow and does not use `compose.yaml`.

Prerequisites:

- Docker Desktop with the WSL 2 backend enabled.
- VS Code with the Microsoft Dev Containers extension.
- AWS CLI credentials on the host under `${USERPROFILE}\\.aws`.

Open the repository folder directly in VS Code, then select **Dev Containers: Reopen in Container**. After the container is ready, run in the integrated container terminal:

```bash
uv run aws-mcp-server
```

The container uses Python 3.12, installs dependencies with `uv sync --dev`, mounts the host AWS configuration read-only at `/home/vscode/.aws`, and forwards port `8000`. Host applications can connect to `http://localhost:8000/mcp`. Stop the server with `Ctrl+C`; closing the Dev Container stops the development environment.

Port forwarding does not provide authentication. Keep Docker Desktop and the host machine trusted, and do not expose this development endpoint beyond the local machine.

### Choosing a local workflow

| Workflow | Purpose | Start command | Python |
| --- | --- | --- | --- |
| Dev Container | Interactive VS Code development | `uv run aws-mcp-server` inside the container | 3.12 |
| Docker Compose | Local development without Dev Containers | `docker compose up --build` | 3.12 |
| Docker image | Production-style deployment or direct Docker run | `docker build` / `docker run` | 3.12 |

## Tool behavior

`list_s3_buckets` returns the names and creation dates of buckets visible to the configured identity. It requires `s3:ListAllMyBuckets`.

`list_s3_objects` accepts `bucket`, optional `prefix`, `max_keys` (1–1000), `include_metadata`, and an optional continuation token returned by an earlier call. Setting `include_metadata=true` performs one `HeadObject` request per listed object and may be slower or require additional permission.

`read_s3_object` accepts `bucket`, `key`, and `max_bytes` (1–10 MiB). It performs a metadata request first and avoids downloading objects larger than the limit. Binary data is base64 encoded so the response remains JSON-safe.

## Development

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Tests mock the boto3 client and do not contact AWS. Before using this server in production, verify IAM policies, bucket policies, network access, profile selection, and the MCP host's process environment separately.

## Architecture and extension points

`src/aws_mcp_server/server.py` owns MCP registration and dependency wiring. `s3_service.py` contains AWS-specific logic behind an injected client. Future services should add a separate service module and a small tool-registration module or registration block without mixing service clients.

Logs go to stderr and include ISO-style timestamps, severity, logger/module, operation, resource, and safe result context. Credentials, authorization headers, object content, and AWS error messages are never logged.

At startup, the server logs a structured JSON diagnostic containing only whether the AWS and MCP environment variables are set, whether boto3 resolved credentials, and whether a region is configured. It never logs environment-variable values or credentials. S3 failures return structured error objects with a safe code, actionable message, and retryability flag; common errors include missing credentials, access denied, missing buckets, missing keys, invalid input, endpoint connectivity, and unexpected internal failures.

## Security and limitations

Use least-privilege IAM policies and do not expose this stdio server to untrusted callers. Object content is intentionally returned to the MCP host; treat the host and model context as authorized recipients. Listing is paginated through the returned continuation token. Live AWS access is not exercised by the unit test suite.
