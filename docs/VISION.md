# Kshetrajna

> **The intelligence that knows the field.**

**Kshetrajna** is an experimental adaptive intelligence layer for Windows that learns how a person uses their computer, understands the context of their current workload, and proposes safe, measurable changes to system resources and the desktop experience.

Instead of treating every user and every workload the same, Kshetrajna builds a lightweight model of:

- how you use applications,
- which applications tend to matter together,
- when certain workflows occur,
- where interaction friction appears,
- and how system pressure changes during those workflows.

It can then use that context to recommend or apply user-approved changes to resource priorities, background activity, workspaces, shortcuts, and interface behavior.

Every adaptation is designed to be:

**Explainable.
Reversible.
Measurable.
Human-approved.**

---

## Why “Kshetrajna”?

*Kshetrajna* is inspired by the Sanskrit concept of the **“knower of the field.”**

In this project, the **field** is the computer itself:

- CPU
- memory
- NPU
- applications
- windows
- background tasks
- power state
- user interaction
- current workflow

Traditional operating systems can observe most of these independently.

Kshetrajna attempts to understand how they relate to **the person currently using the machine**.

---

# The Problem

Modern operating systems are extremely good at managing hardware.

They know:

- which process is in the foreground,
- how much memory an application consumes,
- which threads are busy,
- when CPU pressure is high,
- and when the system should save power.

But they generally do not know:

> **What is the user actually trying to accomplish right now?**

Two people can use the exact same laptop in completely different ways.

Even the same person might use their machine very differently depending on whether they are:

- writing code,
- attending a meeting,
- gaming,
- researching,
- editing media,
- studying,
- or simply browsing.

Yet resource policies and desktop layouts remain mostly generic.

Kshetrajna explores what happens when the operating environment gains a persistent, privacy-conscious model of **user context and workflow importance**.

---

# The Core Idea

Kshetrajna sits above the operating system as an adaptive policy layer.

```text
                    USER
                      │
                      ▼
             Interaction Signals
                      │
                      ▼
               Context Engine
                      │
               "What is happening?"
                      │
                      ▼
               Priority Model
                      │
               "What matters now?"
                      │
                      ▼
                Policy Engine
              ┌───────┼────────┐
              │       │        │
              ▼       ▼        ▼
             CPU     Memory   Power
              │       │        │
              └───────┼────────┘
                      │
                      ▼
                 Windows OS

                      +

                Adaptive Shell
                      │
              workspaces / UI /
             shortcuts / actions
