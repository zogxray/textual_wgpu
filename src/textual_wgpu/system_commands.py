"""SystemCommandsProvider — Textual ``textual.system_commands`` parity."""

from __future__ import annotations

from .command import DiscoveryHit, Hit, Hits, Provider


class SystemCommandsProvider(Provider):
    """Default system commands provider."""

    async def discover(self) -> Hits:
        app = self.app
        if app is None:
            return
        from .command_palette import collect_commands

        for name, doc in collect_commands(app):
            yield DiscoveryHit(name, lambda n=name: app.run_action(n), text=name, help=doc)

    async def search(self, query: str) -> Hits:
        app = self.app
        if app is None:
            return
        from .command_palette import collect_commands
        from .fuzzy import Matcher

        m = Matcher(query)
        for name, doc in collect_commands(app):
            sc = m.match(name)
            if sc > 0:
                yield Hit(sc, m.highlight(name), lambda n=name: app.run_action(n), text=name, help=doc)
