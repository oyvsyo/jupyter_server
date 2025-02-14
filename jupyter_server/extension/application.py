import sys
import re
import logging

from jinja2 import Environment, FileSystemLoader

from traitlets import (
    HasTraits,
    Unicode,
    List,
    Dict,
    Bool,
    default
)
from traitlets.config import Config
from tornado.log import LogFormatter
from tornado.web import RedirectHandler

from jupyter_core.application import JupyterApp

from jupyter_server.serverapp import ServerApp
from jupyter_server.transutils import _
from jupyter_server.utils import url_path_join
from .handler import ExtensionHandlerMixin

# -----------------------------------------------------------------------------
# Util functions and classes.
# -----------------------------------------------------------------------------


def _preparse_for_subcommand(Application, argv):
    """Preparse command line to look for subcommands.
    """
    # Read in arguments from command line.
    if len(argv) == 0:
        return

    # Find any subcommands.
    if Application.subcommands and len(argv) > 0:
        # we have subcommands, and one may have been specified
        subc, subargv = argv[0], argv[1:]
        if re.match(r'^\w(\-?\w)*$', subc) and subc in Application.subcommands:
            # it's a subcommand, and *not* a flag or class parameter
            app = Application()
            app.initialize_subcommand(subc, subargv)
            return app.subapp


def _preparse_for_stopping_flags(Application, argv):
    """Looks for 'help', 'version', and 'generate-config; commands
    in command line. If found, raises the help and version of
    current Application.

    This is useful for traitlets applications that have to parse
    the command line multiple times, but want to control when
    when 'help' and 'version' is raised.
    """
    # Arguments after a '--' argument are for the script IPython may be
    # about to run, not IPython iteslf. For arguments parsed here (help and
    # version), we want to only search the arguments up to the first
    # occurrence of '--', which we're calling interpreted_argv.
    try:
        interpreted_argv = argv[:argv.index('--')]
    except ValueError:
        interpreted_argv = argv

    # Catch any help calls.
    if any(x in interpreted_argv for x in ('-h', '--help-all', '--help')):
        app = Application()
        app.print_help('--help-all' in interpreted_argv)
        app.exit(0)

    # Catch version commands
    if '--version' in interpreted_argv or '-V' in interpreted_argv:
        app = Application()
        app.print_version()
        app.exit(0)

    # Catch generate-config commands.
    if '--generate-config' in interpreted_argv:
        app = Application()
        app.write_default_config()
        app.exit(0)


class ExtensionAppJinjaMixin(HasTraits):
    """Use Jinja templates for HTML templates on top of an ExtensionApp."""

    jinja2_options = Dict(
        help=_("""Options to pass to the jinja2 environment for this
        """)
    ).tag(config=True)

    def _prepare_templates(self):
        # Get templates defined in a subclass.
        self.initialize_templates()
        # Add templates to web app settings if extension has templates.
        if len(self.template_paths) > 0:
            self.settings.update({
                "{}_template_paths".format(self.name): self.template_paths
            })

        # Create a jinja environment for logging html templates.
        self.jinja2_env = Environment(
            loader=FileSystemLoader(self.template_paths),
            extensions=['jinja2.ext.i18n'],
            autoescape=True,
            **self.jinja2_options
        )


        # Add the jinja2 environment for this extension to the tornado settings.
        self.settings.update(
            {
                "{}_jinja2_env".format(self.name): self.jinja2_env
            }
        )

# -----------------------------------------------------------------------------
# ExtensionApp
# -----------------------------------------------------------------------------


class JupyterServerExtensionException(Exception):
    """Exception class for raising for Server extensions errors."""

# -----------------------------------------------------------------------------
# ExtensionApp
# -----------------------------------------------------------------------------


class ExtensionApp(JupyterApp):
    """Base class for configurable Jupyter Server Extension Applications.

    ExtensionApp subclasses can be initialized two ways:
    1. Extension is listed as a jpserver_extension, and ServerApp calls
        its load_jupyter_server_extension classmethod. This is the
        classic way of loading a server extension.
    2. Extension is launched directly by calling its `launch_instance`
        class method. This method can be set as a entry_point in
        the extensions setup.py
    """
    # Subclasses should override this trait. Tells the server if
    # this extension allows other other extensions to be loaded
    # side-by-side when launched directly.
    load_other_extensions = True

    # A useful class property that subclasses can override to
    # configure the underlying Jupyter Server when this extension
    # is launched directly (using its `launch_instance` method).
    serverapp_config = {}

    # Some subclasses will likely override this trait to flip
    # the default value to False if they don't offer a browser
    # based frontend.
    open_browser = Bool(
        True,
        help="""Whether to open in a browser after starting.
        The specific browser used is platform dependent and
        determined by the python standard library `webbrowser`
        module, unless it is overridden using the --browser
        (ServerApp.browser) configuration option.
        """
    ).tag(config=True)

    # The extension name used to name the jupyter config
    # file, jupyter_{name}_config.
    # This should also match the jupyter subcommand used to launch
    # this extension from the CLI, e.g. `jupyter {name}`.
    name = None

    @classmethod
    def get_extension_package(cls):
        return cls.__module__.split('.')[0]

    @classmethod
    def get_extension_point(cls):
        return cls.__module__

    # Extension URL sets the default landing page for this extension.
    extension_url = "/"

    # Extension can configure the ServerApp from the command-line
    classes = [
        ServerApp,
    ]

    # A ServerApp is not defined yet, but will be initialized below.
    serverapp = None

    _log_formatter_cls = LogFormatter

    @default('log_level')
    def _default_log_level(self):
        return logging.INFO

    @default('log_format')
    def _default_log_format(self):
        """override default log format to include date & time"""
        return u"%(color)s[%(levelname)1.1s %(asctime)s.%(msecs).03d %(name)s]%(end_color)s %(message)s"

    static_url_prefix = Unicode(
        help="""Url where the static assets for the extension are served."""
    ).tag(config=True)

    @default('static_url_prefix')
    def _default_static_url_prefix(self):
        static_url = "static/{name}/".format(
            name=self.name
        )
        return url_path_join(self.serverapp.base_url, static_url)

    static_paths = List(Unicode(),
        help="""paths to search for serving static files.

        This allows adding javascript/css to be available from the notebook server machine,
        or overriding individual files in the IPython
        """
    ).tag(config=True)

    template_paths = List(Unicode(),
        help=_("""Paths to search for serving jinja templates.

        Can be used to override templates from notebook.templates.""")
    ).tag(config=True)

    settings = Dict(
        help=_("""Settings that will passed to the server.""")
    ).tag(config=True)

    handlers = List(
        help=_("""Handlers appended to the server.""")
    ).tag(config=True)

    def _config_file_name_default(self):
        """The default config file name."""
        if not self.name:
            return ''
        return 'jupyter_{}_config'.format(self.name.replace('-','_'))

    def initialize_settings(self):
        """Override this method to add handling of settings."""
        pass

    def initialize_handlers(self):
        """Override this method to append handlers to a Jupyter Server."""
        pass

    def initialize_templates(self):
        """Override this method to add handling of template files."""
        pass

    def _prepare_config(self):
        """Builds a Config object from the extension's traits and passes
        the object to the webapp's settings as `<name>_config`.
        """
        traits = self.class_own_traits().keys()
        self.extension_config = Config({t: getattr(self, t) for t in traits})
        self.settings['{}_config'.format(self.name)] = self.extension_config

    def _prepare_settings(self):
        # Make webapp settings accessible to initialize_settings method
        webapp = self.serverapp.web_app
        self.settings.update(**webapp.settings)

        # Add static and template paths to settings.
        self.settings.update({
            "{}_static_paths".format(self.name): self.static_paths,
            "{}".format(self.name): self,
        })

        # Get setting defined by subclass using initialize_settings method.
        self.initialize_settings()

        # Update server settings with extension settings.
        webapp.settings.update(**self.settings)

    def _prepare_handlers(self):
        webapp = self.serverapp.web_app

        # Get handlers defined by extension subclass.
        self.initialize_handlers()

        # prepend base_url onto the patterns that we match
        new_handlers = []
        for handler_items in self.handlers:
            # Build url pattern including base_url
            pattern = url_path_join(webapp.settings['base_url'], handler_items[0])
            handler = handler_items[1]

            # Get handler kwargs, if given
            kwargs = {}
            if issubclass(handler, ExtensionHandlerMixin):
                kwargs['name'] = self.name

            try:
                kwargs.update(handler_items[2])
            except IndexError:
                pass

            new_handler = (pattern, handler, kwargs)
            new_handlers.append(new_handler)

        # Add static endpoint for this extension, if static paths are given.
        if len(self.static_paths) > 0:
            # Append the extension's static directory to server handlers.
            static_url = url_path_join(self.static_url_prefix, "(.*)")

            # Construct handler.
            handler = (
                static_url,
                webapp.settings['static_handler_class'],
                {'path': self.static_paths}
            )
            new_handlers.append(handler)

        webapp.add_handlers('.*$', new_handlers)

    def _prepare_templates(self):
        # Add templates to web app settings if extension has templates.
        if len(self.template_paths) > 0:
            self.settings.update({
                "{}_template_paths".format(self.name): self.template_paths
            })
        self.initialize_templates()

    @classmethod
    def _jupyter_server_config(cls):
        base_config = {
            "ServerApp": {
                "jpserver_extensions": {cls.get_extension_package(): True},
                "default_url": cls.extension_url
            }
        }
        base_config["ServerApp"].update(cls.serverapp_config)
        return base_config

    def _link_jupyter_server_extension(self, serverapp):
        """Link the ExtensionApp to an initialized ServerApp.

        The ServerApp is stored as an attribute and config
        is exchanged between ServerApp and `self` in case
        the command line contains traits for the ExtensionApp
        or the ExtensionApp's config files have server
        settings.
        """
        self.serverapp = serverapp
        # Load config from an ExtensionApp's config files.
        self.load_config_file()
        # ServerApp's config might have picked up
        # config for the ExtensionApp. We call
        # update_config to update ExtensionApp's
        # traits with these values found in ServerApp's
        # config.
        # ServerApp config ---> ExtensionApp traits
        self.update_config(self.serverapp.config)
        # Use ExtensionApp's CLI parser to find any extra
        # args that passed through ServerApp and
        # now belong to ExtensionApp.
        self.parse_command_line(self.serverapp.extra_args)
        # If any config should be passed upstream to the
        # ServerApp, do it here.
        # i.e. ServerApp traits <--- ExtensionApp config
        self.serverapp.update_config(self.config)

    @classmethod
    def initialize_server(cls, argv=[], load_other_extensions=True, **kwargs):
        """Creates an instance of ServerApp where this extension is enabled
        (superceding disabling found in other config from files).

        This is necessary when launching the ExtensionApp directly from
        the `launch_instance` classmethod.
        """
        # The ExtensionApp needs to add itself as enabled extension
        # to the jpserver_extensions trait, so that the ServerApp
        # initializes it.
        config = Config(cls._jupyter_server_config())
        serverapp = ServerApp.instance(**kwargs, argv=[], config=config)
        serverapp.initialize(argv=argv, find_extensions=load_other_extensions)
        # Inform the serverapp that this extension app started the app.
        serverapp._starter_app_name = cls.name
        return serverapp

    def initialize(self):
        """Initialize the extension app. The
        corresponding server app and webapp should already
        be initialized by this step.

        1) Appends Handlers to the ServerApp,
        2) Passes config and settings from ExtensionApp
        to the Tornado web application
        3) Points Tornado Webapp to templates and
        static assets.
        """
        if not self.serverapp:
            msg = (
                "This extension has no attribute `serverapp`. "
                "Try calling `.link_to_serverapp()` before calling "
                "`.initialize()`."
            )
            raise JupyterServerExtensionException(msg)

        self._prepare_config()
        self._prepare_templates()
        self._prepare_settings()
        self._prepare_handlers()

    def start(self):
        """Start the underlying Jupyter server.

        Server should be started after extension is initialized.
        """
        super(ExtensionApp, self).start()
        # Start the server.
        self.serverapp.start()

    def stop(self):
        """Stop the underlying Jupyter server.
        """
        self.serverapp.stop()
        self.serverapp.clear_instance()

    @classmethod
    def _load_jupyter_server_extension(cls, serverapp):
        """Initialize and configure this extension, then add the extension's
        settings and handlers to the server's web application.
        """
        extension_manager = serverapp.extension_manager
        try:
            # Get loaded extension from serverapp.
            point = extension_manager.extension_points[cls.name]
            extension = point.app
        except KeyError:
            extension = cls()
            extension._link_jupyter_server_extension(serverapp)
        extension.initialize()
        return extension

    @classmethod
    def load_classic_server_extension(cls, serverapp):
        """Enables extension to be loaded as classic Notebook (jupyter/notebook) extension.
        """
        extension = cls()
        extension.serverapp = serverapp
        extension.load_config_file()
        extension.update_config(serverapp.config)
        extension.parse_command_line(serverapp.extra_args)
        # Add redirects to get favicons from old locations in the classic notebook server
        extension.handlers.extend([
            (r"/static/favicons/favicon.ico", RedirectHandler,
                {"url": url_path_join(serverapp.base_url, "static/base/images/favicon.ico")}),
            (r"/static/favicons/favicon-busy-1.ico", RedirectHandler,
                {"url": url_path_join(serverapp.base_url, "static/base/images/favicon-busy-1.ico")}),
            (r"/static/favicons/favicon-busy-2.ico", RedirectHandler,
                {"url": url_path_join(serverapp.base_url, "static/base/images/favicon-busy-2.ico")}),
            (r"/static/favicons/favicon-busy-3.ico", RedirectHandler,
                {"url": url_path_join(serverapp.base_url, "static/base/images/favicon-busy-3.ico")}),
            (r"/static/favicons/favicon-file.ico", RedirectHandler,
                {"url": url_path_join(serverapp.base_url, "static/base/images/favicon-file.ico")}),
            (r"/static/favicons/favicon-notebook.ico", RedirectHandler,
                {"url": url_path_join(serverapp.base_url, "static/base/images/favicon-notebook.ico")}),
            (r"/static/favicons/favicon-terminal.ico", RedirectHandler,
                {"url": url_path_join(serverapp.base_url, "static/base/images/favicon-terminal.ico")}),
            (r"/static/logo/logo.png", RedirectHandler,
                {"url": url_path_join(serverapp.base_url, "static/base/images/logo.png")}),
        ])
        extension.initialize()

    @classmethod
    def launch_instance(cls, argv=None, **kwargs):
        """Launch the extension like an application. Initializes+configs a stock server
        and appends the extension to the server. Then starts the server and routes to
        extension's landing page.
        """
        # Handle arguments.
        if argv is None:
            args = sys.argv[1:]  # slice out extension config.
        else:
            args = argv
        # Check for subcommands
        subapp = _preparse_for_subcommand(cls, args)
        if subapp:
            subapp.start()
        else:
            # Check for help, version, and generate-config arguments
            # before initializing server to make sure these
            # arguments trigger actions from the extension not the server.
            _preparse_for_stopping_flags(cls, args)
            # Get a jupyter server instance.
            serverapp = cls.initialize_server(
                argv=args,
                load_other_extensions=cls.load_other_extensions
            )
            # Log if extension is blocking other extensions from loading.
            if not cls.load_other_extensions:
                serverapp.log.info(
                    "{ext_name} is running without loading "
                    "other extensions.".format(ext_name=cls.name)
                )
            serverapp.start()
