from yoyo import step


steps = [
    step(
        """
        alter table papers
        add column if not exists starred boolean not null default false
        """
    )
]
