Package for monitoring disk space, multiple files and multiple processes.
This is useful for ensuring that a logging program continues to work correctly.
If errors are found then an email is sent.

See comments at top of `babysitter/babysitter.py` for details.

Also see `power_babysitter.py` for a working example of how to use the babysitter framework.
`power_babysitter.py` can be used to supervise [`rfm_ecomanager_logger`](https://github.com/JackKelly/rfm_ecomanager_logger)
and [`powerstats`](https://github.com/JackKelly/powerstats).

An alternative would be the [Supervisor](http://supervisord.org/) project.