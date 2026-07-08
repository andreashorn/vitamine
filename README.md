# VitaMine

VitaMine is a local CV and biosketch workbench for curating academic profile data, publications, and export-ready CV variants.

## Start

```sh
python3 -m vitamine.scripts.open_cv_tool
```

The app opens at:

```text
http://127.0.0.1:8765
```

By default VitaMine loads `data/example.sqlite`, which is an example database copied from Andreas Horn's CV project.

## Databases

- `data/example.sqlite` is the included example database.
- `data/active_db.txt` stores which database VitaMine is currently using.
- Use the Database panel in the app to create a new blank database or switch back to the example database.

You can also override the active database for a process:

```sh
VITAMINE_DB=/path/to/my.sqlite python3 -m vitamine.scripts.open_cv_tool
```

## Notes

Generated exports and logs are written to `output/` and are ignored by git.
