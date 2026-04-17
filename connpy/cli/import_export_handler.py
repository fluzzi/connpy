import os
import sys
import inquirer
from .. import printer
from ..services.exceptions import ConnpyError
from .forms import Forms

class ImportExportHandler:
    def __init__(self, app):
        self.app = app
        self.forms = Forms(app)

    def dispatch_import(self, args):
        file_path = args.data[0]
        try:
            printer.warning("This could overwrite your current configuration!")
            question = [inquirer.Confirm("import", message=f"Are you sure you want to import {file_path}?")]
            confirm = inquirer.prompt(question)
            if confirm == None or not confirm["import"]:
                sys.exit(7)
                
            self.app.services.import_export.import_from_file(file_path)
            printer.success(f"File {file_path} imported successfully.")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)

    def dispatch_export(self, args):
        file_path = args.data[0]
        folders = args.data[1:] if len(args.data) > 1 else None
        try:
            self.app.services.import_export.export_to_file(file_path, folders=folders)
            printer.success(f"File {file_path} generated successfully")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)
        sys.exit()

    def bulk(self, args):
        if args.file and os.path.isfile(args.file[0]):
            with open(args.file[0], 'r') as f:
                lines = f.readlines()

            # Expecting exactly 2 lines
            if len(lines) < 2:
                printer.error("The file must contain at least two lines: one for nodes, one for hosts.")
                sys.exit(11)

            nodes = lines[0].strip()
            hosts = lines[1].strip()
            newnodes = self.forms.questions_bulk(nodes, hosts)
        else:
            newnodes = self.forms.questions_bulk()

        if newnodes == False:
            sys.exit(7)

        if not self.app.case:
            newnodes["location"] = newnodes["location"].lower()
            newnodes["ids"] = newnodes["ids"].lower()

        # Handle the case where location might be a file reference (e.g. from a prompt)
        location = newnodes["location"]
        if location.startswith("@") and "/" in location:
            # Extract the actual @folder part (e.g. @testall from @testall/.folders_cache.txt)
            location = location.split("/")[0]
            newnodes["location"] = location

        ids = newnodes["ids"].split(",")
        # Append location to each id for proper folder assignment
        location = newnodes["location"]
        if location:
            ids = [f"{i}{location}" for i in ids]
            
        hosts = newnodes["host"].split(",")

        try:
            count = self.app.services.nodes.bulk_add(ids, hosts, newnodes)
            if count > 0:
                printer.success(f"Successfully added {count} nodes.")
            else:
                printer.info("0 nodes added")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)
