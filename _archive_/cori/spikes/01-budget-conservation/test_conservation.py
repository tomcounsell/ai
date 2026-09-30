"""Hypothesis stateful test: for any sequence of delegate and consume calls,
no node's ledger outflow exceeds its budget, and the DB agrees with a pure
Python model of the same operations."""

import asyncio
import uuid

import psycopg
import pytest
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, Bundle

import kernel

pytestmark = pytest.mark.skipif(False, reason="needs the spike cluster; run via run.sh")


class Tree(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.conn = self.loop.run_until_complete(
            psycopg.AsyncConnection.connect(kernel.dsn_for("cori_kernel"))
        )
        self.budget: dict[str, int] = {}
        self.outflow: dict[str, int] = {}
        self.root = f"r-{uuid.uuid4().hex[:8]}"
        self.loop.run_until_complete(kernel.create_root(self.conn, self.root, 1000))
        self.budget[self.root] = 1000
        self.outflow[self.root] = 0

    nodes = Bundle("nodes")

    @rule(target=nodes)
    def seed(self):
        return self.root

    @rule(target=nodes, parent=nodes, amount=st.integers(min_value=1, max_value=400))
    def delegate(self, parent, amount):
        child = f"c-{uuid.uuid4().hex[:8]}"
        expected_ok = amount <= self.budget[parent] - self.outflow[parent]
        try:
            self.loop.run_until_complete(
                kernel.delegate(self.conn, parent, child, amount)
            )
            ok = True
        except kernel.BudgetExceeded:
            ok = False
        assert ok == expected_ok, (parent, amount, ok, expected_ok)
        if ok:
            self.outflow[parent] += amount
            self.budget[child] = amount
            self.outflow[child] = 0
            return child
        return parent

    @rule(node=nodes, amount=st.integers(min_value=1, max_value=400))
    def consume(self, node, amount):
        expected_ok = amount <= self.budget[node] - self.outflow[node]
        try:
            self.loop.run_until_complete(kernel.consume(self.conn, node, amount))
            ok = True
        except kernel.BudgetExceeded:
            ok = False
        assert ok == expected_ok
        if ok:
            self.outflow[node] += amount

    @invariant()
    def conserved(self):
        for n, b in self.budget.items():
            assert self.outflow[n] <= b
        overs = self.loop.run_until_complete(kernel.audit(self.conn))
        assert overs == [], overs

    def teardown(self):
        self.loop.run_until_complete(self.conn.close())
        self.loop.close()


TestTree = Tree.TestCase
TestTree.settings = settings(max_examples=60, stateful_step_count=30, deadline=None)
