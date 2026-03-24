# Agents

Configuration for the multi-agent system. Defines the Supervisor and available specialist sub-agents.

## Supervisor

- Receives all incoming messages
- Delegates to specialists when appropriate
- Delegation depth limit: 2

## Specialists

- infrastructure: AWS resource management
- code: Code generation and testing
- communications: Messaging and notifications
- research: Information gathering
