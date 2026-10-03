Part of [architecture.md](architecture.md).

# The objective tree

Serves Mission item 4: Tom delegates a feature, then a workflow, then a
product.

**Built.** One task record. Both experiments needed nothing more.

**Design.** A task is a node; a node too large for one session is split
into children, each a task with its own Brief. Rules the kernel enforces:

- A child's spending rolls up into its parent's reported spending; no
  money is carved from a parent.
- A child's effect ceiling never exceeds its parent's; capabilities are
  attenuated on dispatch and the kernel refuses a request it cannot meet in
  full rather than clipping it [11].
- Decomposition cuts by outcome [13], one agent per leaf. Fan-out and depth
  are bounded by the leaf criterion (one session), never by a count.
- A child's report lands in the tree and the parent reads its summary.
- Stopping a node fences its subtree by generation (see Stop).
