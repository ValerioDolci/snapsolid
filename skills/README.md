# Claude Code Skills

Snapsolid includes two [Claude Code](https://claude.ai/claude-code) slash commands that let an AI agent operate the pipeline autonomously.

## Available skills

| Command | Description |
|---------|-------------|
| `/snapsolid` | Run the full pipeline — photos in, printable STL out |
| `/snapsolid-check` | Analyze an STL file — check printability, topology, dimensions |

## Installation

Copy the skill files to your Claude Code commands directory:

```bash
mkdir -p .claude/commands
cp skills/snapsolid.md .claude/commands/
cp skills/snapsolid-check.md .claude/commands/
```

Then restart Claude Code. The commands will be available as `/snapsolid` and `/snapsolid-check`.

## Usage

```
/snapsolid /path/to/drone_photos --detail raw --decimate --scale-to-mm 150
```

```
/snapsolid-check /path/to/model.stl
```
