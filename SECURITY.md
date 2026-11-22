# Security policy

fLMCP runs locally: the MCP server talks to FL Studio through files in your FL
user-data folder and never opens a network port. Things worth knowing:

* `fl_call_raw` and the `project_*` tools can change or overwrite your project.
  MCP clients show these as destructive — review before approving.
* Piano-roll and render tools send keystrokes to the FL Studio window.
* Anyone who can write to `Documents/Image-Line/FL Studio/Settings/Hardware/fLMCP Bridge`
  can send commands to FL while the bridge is enabled.

Please report vulnerabilities privately via
[GitHub security advisories](https://github.com/geezoria/FLStudioMCP/security/advisories/new)
rather than a public issue.
