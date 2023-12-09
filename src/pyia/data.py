""" Data structures. """

# Standard library
import pathlib
import warnings
from typing import Any, Dict, List, Optional, Tuple, Union

# Third-party
import astropy.coordinates as coord
import astropy.units as u
import numpy as np
import numpy.typing as npt
from astropy.table import Column, Table
from astropy.time import Time

from pyia.extinction import get_ext_dr2_Babusiaux

__all__ = ["GaiaData"]

length = u.get_physical_type("length")
angle = u.get_physical_type("angle")
ang_vel = u.get_physical_type("angular velocity")
time = u.get_physical_type("time")
vel = u.get_physical_type("speed")

# This is from reading the data model
gaia_unit_map = {
    "ra": u.degree,
    "dec": u.degree,
    "parallax": u.milliarcsecond,
    "pmra": u.milliarcsecond / u.year,
    "pmdec": u.milliarcsecond / u.year,
    "radial_velocity": u.km / u.s,
    "ra_error": u.milliarcsecond,
    "dec_error": u.milliarcsecond,
    "parallax_error": u.milliarcsecond,
    "pmra_error": u.milliarcsecond / u.year,
    "pmdec_error": u.milliarcsecond / u.year,
    "radial_velocity_error": u.km / u.s,
    "astrometric_excess_noise": u.mas,
    "astrometric_weight_al": 1 / u.mas**2,
    "astrometric_pseudo_colour": 1 / u.micrometer,
    "astrometric_pseudo_colour_error": 1 / u.micrometer,
    "astrometric_sigma5d_max": u.mas,
    "phot_g_mean_flux": u.photon / u.s,
    "phot_g_mean_flux_error": u.photon / u.s,
    "phot_g_mean_mag": u.mag,
    "phot_bp_mean_flux": u.photon / u.s,
    "phot_bp_mean_flux_error": u.photon / u.s,
    "phot_bp_mean_mag": u.mag,
    "phot_rp_mean_flux": u.photon / u.s,
    "phot_rp_mean_flux_error": u.photon / u.s,
    "phot_rp_mean_mag": u.mag,
    "bp_rp": u.mag,
    "bp_g": u.mag,
    "g_rp": u.mag,
    "rv_template_teff": u.K,
    "l": u.degree,
    "b": u.degree,
    "ecl_lon": u.degree,
    "ecl_lat": u.degree,
    "teff_val": u.K,
    "teff_percentile_lower": u.K,
    "teff_percentile_upper": u.K,
    "a_g_val": u.mag,
    "a_g_percentile_lower": u.mag,
    "a_g_percentile_upper": u.mag,
    "e_bp_min_rp_val": u.mag,
    "e_bp_min_rp_percentile_lower": u.mag,
    "e_bp_min_rp_percentile_upper": u.mag,
    "radius_val": u.Rsun,
    "radius_percentile_lower": u.Rsun,
    "radius_percentile_upper": u.Rsun,
    "lum_val": u.Lsun,
    "lum_percentile_lower": u.Lsun,
    "lum_percentile_upper": u.Lsun,
    "ref_epoch": u.year,
}

REF_EPOCH = {
    "DR2": Time(2015.5, format="jyear"),
    "EDR3": Time(2016.0, format="jyear"),
    "DR3": Time(2016.0, format="jyear"),
}
LATEST_RELEASE = "DR3"

# Mapping from key = dtype chars to value = fill value
# https://numpy.org/doc/stable/reference/arrays.dtypes.html
_fill_values = {"i": -1, "u": 0, "f": np.nan, "d": np.nan, "U": "", "S": ""}


class GaiaData:
    """Class for loading and interacting with data from the Gaia mission. This
    should work with data from any data release, i.e., DR1 gaia_source or TGAS,
    or DR2 gaia_source, or EDR3, DR3 gaia_source.

    Parameters
    ----------
    data : `astropy.table.Table`, `pandas.DataFrame`, dict_like, str
        This must be pre-loaded data as any of the types listed above, or a
        string filename containing a table that is readable by
        `astropy.table.Table.read`.
    """

    def __init__(
        self,
        data: Union[Table, str, pathlib.Path, Dict[str, Any]],
        distance_colname: str = "parallax",
        distance_error_colname: str = "parallax_error",
        distance_unit: Union[u.Unit, str, None] = None,
        radial_velocity_colname: str = "radial_velocity",
        radial_velocity_error_colname: str = "radial_velocity_error",
        radial_velocity_unit: Union[u.Unit, str, None] = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(data, Table):
            if isinstance(data, (str, pathlib.Path)):
                if any("fits" in x for x in pathlib.Path(data).suffixes):
                    kwargs.setdefault("unit_parse_strict", "silent")
                data = Table.read(data, **kwargs)

            else:
                # the dict-like object might have Quantity's, so we want to
                # preserve any units
                data = Table(data, **kwargs)

        # HACK: make sure table isn't masked, until astropy fully supports masked
        # quantities
        if data.masked:
            cols = []
            for c in data.colnames:
                col = data[c]
                col.mask = None
                cols.append(Column(col))
            data = Table(cols, copy=False)

        # Create a copy of the default unit map
        self.units = gaia_unit_map.copy()

        # Store the source table
        self.data = data

        # Update the unit map with the table units
        self._invalid_units = {}
        for c in data.colnames:
            if data[c].unit is not None:
                try:
                    self.units[c] = u.Unit(str(data[c].unit))
                except ValueError:
                    self._invalid_units[c] = data[c].unit

        self.radial_velocity_colname = str(radial_velocity_colname)
        self.radial_velocity_error_colname = str(radial_velocity_error_colname)
        self.distance_colname = str(distance_colname)
        self.distance_error_colname = str(distance_error_colname)
        self._extra_kw: Dict[str, Union[str, u.Unit]] = {
            "distance_colname": self.distance_colname,
            "distance_error_colname": self.distance_error_colname,
            "distance_unit": distance_unit,
            "radial_velocity_colname": self.radial_velocity_colname,
            "radial_velocity_error_colname": self.radial_velocity_error_colname,
            "radial_velocity_unit": radial_velocity_unit,
        }

        for colname, default, unit in zip(
            [
                self.radial_velocity_colname,
                self.radial_velocity_error_colname,
                self.distance_colname,
                self.distance_error_colname,
            ],
            ["radial_velocity", "radial_velocity_error", "parallax", "parallax_error"],
            [radial_velocity_unit, radial_velocity_unit, distance_unit, distance_unit],
        ):
            if colname not in self.data.colnames:
                msg = f"Column '{colname}' not found in data table."
                raise ValueError(msg)

            if colname != default:
                col = self.data[colname]
                if not hasattr(col, "unit") or col.unit == u.one or col.unit is None:
                    if unit is None:
                        msg = (
                            "If you use a custom column for radial velocity or "
                            "distance, and/or their respective errors, that column "
                            "must have an associated astropy unit, or you must specify "
                            f"the corresponding ..._unit keyword argument. {colname} "
                            "does not have a unit."
                        )
                        raise ValueError(msg)
                    self.units[colname] = unit

                elif unit is not None:
                    self.data[colname] = u.Quantity(col).to_value(unit)
                    self.units[colname] = unit

        self._has_rv = self.radial_velocity_colname in self.data.colnames

        # For caching later
        self._cache: Dict[Any, Any] = {}

    @classmethod
    def from_query(
        cls,
        query_str: str,
        login_info: Optional[Dict[str, str]] = None,
        verbose: bool = False,
    ) -> "GaiaData":
        """
        Run the specified query and return a `GaiaData` instance with the
        returned data.

        This is meant only to be used for quick queries to the main Gaia science
        archive. For longer queries and more customized usage, use TAP access to
        any of the Gaia mirrors with, e.g., astroquery or pyvo.

        This requires ``astroquery`` to be installed.

        Parameters
        ----------
        query_str : str
            The string ADQL query to execute.
        login_info : dict, optional
            Username and password for the Gaia science archive as keys "user"
            and "password". If not specified, will use anonymous access, subject
            to the query limits.

        Returns
        -------
        gaiadata : `GaiaData`
            An instance of this object.

        """
        try:
            from astroquery.gaia import Gaia
        except ImportError as err:
            msg = (
                "Failed to import astroquery. To use the from_query() classmethod, you "
                "must first install astroquery, e.g., with pip: \n\tpip install "
                "astroquery"
            )
            raise ImportError(msg) from err

        if login_info is not None:
            Gaia.login(**login_info)

        job = Gaia.launch_job_async(query_str, verbose=verbose)
        tbl = job.get_results()

        return cls(tbl)

    @classmethod
    def from_source_id(
        cls,
        source_id: int,
        source_id_dr: Optional[str] = None,
        data_dr: Optional[str] = None,
        **kwargs: Any,
    ) -> "GaiaData":
        """Retrieve data from a DR for a given Gaia source_id in a DR.

        Useful if you have, e.g., a DR2 source_id and want EDR3 data.

        Parameters
        ----------
        source_id : int
            The Gaia source_id
        source_id_dr : str, optional
            The data release slug (e.g., 'dr2' or 'edr3') for the input
            source_id. Defaults to the latest data release.
        data_dr : str, optional
            The data release slug (e.g., 'dr2' or 'edr3') to retrieve data from.
            Defaults to the latest data release.
        **kwargs
            Passed to ``from_query()``

        Returns
        -------
        gaiadata : `GaiaData`
            An instance of this object.
        """

        join_tables = {
            "dr1": {"dr2": "gaiadr2.dr1_neighbourhood"},
            "dr2": {"edr3": "gaiaedr3.dr2_neighbourhood"},
        }
        source_id_prefixes = {"dr1": "dr1", "dr2": "dr2", "edr3": "dr3"}

        if source_id_dr is None:
            source_id_dr = LATEST_RELEASE.lower()

        if data_dr is None:
            data_dr = LATEST_RELEASE.lower()

        if source_id_dr == data_dr:
            query_str = f"""
                SELECT * FROM gaia{data_dr}.gaia_source AS gaia
                WHERE gaia.source_id = {source_id}
            """
            return cls.from_query(query_str, **kwargs)

        dr1, dr2 = sorted([source_id_dr, data_dr])

        try:
            join_table = join_tables[dr1][dr2]
            source_id_pref = source_id_prefixes[source_id_dr]
            data_pref = source_id_prefixes[data_dr]
        except KeyError as err:
            msg = f"Failed to find join table for {source_id_dr} " f"to {data_dr}"
            raise KeyError(msg) from err

        query_str = f"""
            SELECT * FROM gaia{data_dr}.gaia_source AS gaia
            JOIN {join_table} AS old_gaia
                ON gaia.source_id = old_gaia.{data_pref}_source_id
            WHERE old_gaia.{source_id_pref}_source_id = {source_id}
        """
        return cls.from_query(query_str, **kwargs)

    ##########################################################################
    # Python internal
    #
    def __getattr__(self, name: Any) -> Union[npt.NDArray, u.Quantity]:
        # to prevent recursion errors:
        # nedbatchelder.com/blog/201010/surprising_getattr_recursion.html
        if name in ["data", "units"]:
            raise AttributeError()

        try:
            coldata = self.data[name]
            if hasattr(coldata, "mask") and coldata.mask is not None:
                arr = coldata.filled(_fill_values.get(coldata.dtype.char, None))
            else:
                arr = coldata
            arr = np.asarray(arr)

            if name in self.units:
                return arr * self.units[name]
        except Exception as err:
            msg = "Failed to get attribute."
            raise AttributeError(msg) from err

        return arr

    def __setattr__(self, name: Any, val: Any) -> None:
        if name in ["data", "units"]:
            # needs to be here to catch the first time we enter this func.
            super().__setattr__(name, val)

        elif name in self.units:
            if not hasattr(val, "unit"):
                msg = (
                    f"To set data for column '{name}', you must provide a Quantity-like"
                    " object (with units)."
                )
                raise ValueError(msg)
            self.data[name] = val
            self.units[name] = val.unit

        elif name in self.data.columns:
            self.data[name] = val

        else:
            super().__setattr__(name, val)

    def __dir__(self) -> List[str]:
        return list(super().__dir__()) + [str(k) for k in self.data.columns]

    def __getitem__(
        self, slc: Union[int, slice, npt.NDArray]
    ) -> Union["GaiaData", Any]:
        if isinstance(slc, int):
            slc = slice(slc, slc + 1)
        elif isinstance(slc, str):
            return self.__getattr__(slc)
        return self.__class__(self.data[slc], **self._extra_kw)

    def __setitem__(self, name: Any, val: Any) -> None:
        if hasattr(val, "unit"):
            self.data[name] = val.value
            self.units[name] = val.unit
        else:
            self.data[name] = val

    def __len__(self) -> int:
        return len(self.data)

    def __str__(self) -> str:
        names = ["ra", "dec", "parallax", "pmra", "pmdec"]
        if self._has_rv:
            names.append("radial_velocity")
        return str(self.data[names])

    def __repr__(self) -> str:
        return f"<GaiaData: {len(self):d} rows>"

    ##########################################################################
    # Computed and convenience quantities
    #
    def get_pm(
        self, frame: Union[str, coord.BaseCoordinateFrame] = "icrs"
    ) -> u.Quantity[u.mas / u.yr]:
        """Get the 2D proper motion array in the specified frame

        Parameters
        ----------
        frame : str, `~astropy.coordinates.BaseCoordinateFrame`
            The coordinate frame to return the proper motion vector in. Has shape `(nrows, 2)`
        """
        if frame == "icrs" or isinstance(frame, coord.ICRS):
            _u = self.pmra.unit
            pm = np.vstack((self.pmra.value, self.pmdec.to(_u).value)).T * _u
        else:
            c = self.get_skycoord(distance=False, radial_velocity=False)
            fr = c.to_frame(frame)
            diff = fr.data.differentials["s"]
            _u = diff.d_lon_coslat.unit
            pm = (
                np.vstack((diff.d_lon_coslat.to_value(_u), diff.d_lat.to_value(_u))).T
                * _u
            )
        return pm

    @u.quantity_input  # (equivalencies=u.parallax())
    def get_distance(
        self,
        min_parallax: Optional[u.Quantity[angle]] = None,
        parallax_fill_value: float = np.nan,
        allow_negative: bool = False,
    ) -> u.Quantity:
        """Compute distance from parallax (by inverting the parallax) using
        `~astropy.coordinates.Distance`.

        Parameters
        ----------
        min_parallax : `~astropy.units.Quantity` (optional)
            If `min_parallax` specified, the parallaxes are clipped to this
            values (and it is also used to replace NaNs).
        allow_negative : bool (optional)
            This is passed through to `~astropy.coordinates.Distance`.

        Returns
        -------
        dist : `~astropy.coordinates.Distance`
            A ``Distance`` object with the data.
        """

        if self.distance_colname != "parallax":
            return coord.Distance(
                getattr(self, self.distance_colname), allow_negative=allow_negative
            )

        plx = self.parallax.copy()

        if np.isnan(parallax_fill_value):
            parallax_fill_value = parallax_fill_value * u.mas

        if min_parallax is not None:
            clipped = plx < min_parallax
            clipped |= ~np.isfinite(plx)
            plx[clipped] = parallax_fill_value

        return coord.Distance(parallax=plx, allow_negative=allow_negative)

    @property
    def distance(self) -> u.Quantity:
        """Assumes 1/parallax. Has shape `(nrows,)`.

        This attribute will raise an error when there are negative or zero
        parallax values. For more flexible retrieval of distance values and
        auto-filling bad values, use the .get_distance() method."""
        return self.get_distance(allow_negative=True)

    @property
    def distmod(self) -> u.Quantity:
        """Distance modulus"""
        return self.distance.distmod

    def get_radial_velocity(
        self, fill_value: Optional[float] = None
    ) -> u.Quantity[u.km / u.s]:
        """Return radial velocity but with invalid values filled with the
        specified fill value.

        Parameters
        ----------
        fill_value : `~astropy.units.Quantity` (optional)
            If not ``None``, fill any invalid values with the specified value.
        """
        if self.radial_velocity_colname != "radial_velocity":
            rv = getattr(self, self.radial_velocity_colname)
        else:
            rv = self.radial_velocity.copy()

        if fill_value is not None:
            rv[~np.isfinite(rv)] = fill_value * rv.unit

        return rv

    @property
    def vtan(self) -> u.Quantity[u.km / u.s]:
        """Tangential velocity computed using the proper motion and inverse parallax as
        the distance. Has shape `(nrows, 2)`
        """
        d = self.distance
        with u.set_enabled_equivalencies(u.dimensionless_angles()):
            vra = (self.pmra * d).to_value(u.km / u.s)
            vdec = (self.pmdec * d).to_value(u.km / u.s)
        return np.vstack((vra, vdec)).T * u.km / u.s

    def get_cov(
        self,
        RAM_threshold: u.Quantity = 1 * u.gigabyte,
        coords: Optional[List[str]] = None,
        units: Optional[Dict[str, u.Unit]] = None,
        warn_missing_corr: bool = False,
    ) -> Tuple[npt.NDArray, Dict[str, u.Unit]]:
        """The Gaia data tables contain correlation coefficients and standard
        deviations for (ra, dec, parallax, pm_ra, pm_dec), but for most analyses we need
        covariance matrices. This converts the data provided by Gaia into covariance
        matrices.

        If a radial velocity exists, this also contains the radial velocity variance. If
        radial velocity doesn't exist, that diagonal element is set to inf.

        The default units of the covariance matrix are [degree, degree, mas, mas/yr,
        mas/yr, km/s], but this can be modified by passing in a dictionary with new
        units. For example, to change just the default ra, dec units for the covariance
        matrix, you can pass in::

            units=dict(ra=u.radian, dec=u.radian)

        Parameters
        ----------
        RAM_threshold : `astropy.units.Quantity`
            Raise an error if the expected covariance array is larger than the specified
            threshold. Set to ``None`` to disable this checking.
        """

        if RAM_threshold is not None:
            # Raise error if the user is going to blow up their RAM
            estimated_RAM = 6 * 6 * len(self) * 8 * u.bit
            if estimated_RAM > RAM_threshold:
                msg = (
                    "Estimated RAM usage for generating covariance matrices is larger "
                    "than the specified threshold. Use the argument: "
                    "`RAM_threshold=None` to disable this check"
                )
                raise RuntimeError(msg)

        if coords is None:
            coords = [
                "ra",
                "dec",
                self.distance_colname,
                "pmra",
                "pmdec",
                self.radial_velocity_colname,
            ]

        if units is None:
            units = {}

        for name in coords:
            units.setdefault(name, self.units[name])

        # The full returned matrix
        C = np.zeros((len(self), len(coords), len(coords)))

        # pre-load the diagonal
        for i, name in enumerate(coords):
            if name == self.distance_colname:
                err = getattr(self, self.distance_error_colname)
            elif name == self.radial_velocity_colname:
                err = getattr(self, self.radial_velocity_error_colname)
            elif name + "_error" in self.data.colnames:
                err = getattr(self, name + "_error")
            else:
                err = 0.0 * units[name]
            C[:, i, i] = err.to_value(units[name]) ** 2

        for i, name1 in enumerate(coords):
            for j, name2 in enumerate(coords):
                if j <= i:
                    continue

                if f"{name1}_{name2}_corr" in self.data.colnames:
                    corr = getattr(self, f"{name1}_{name2}_corr")
                else:
                    corr = 0.0

                    if warn_missing_corr:
                        warnings.warn(
                            f"Missing correlation coefficient for {name1} and {name2}. "
                            "Setting to zero.",
                            RuntimeWarning,
                            stacklevel=1,
                        )

                # We don't need to worry about units here because the diagonal
                # values have already been converted
                C[:, i, j] = corr * np.sqrt(C[:, i, i] * C[:, j, j])
                C[:, j, i] = C[:, i, j]

        return C, units

    def get_ebv(self, dustmaps_cls: Optional[Any] = None) -> npt.NDArray:
        """Compute the E(B-V) reddening at this location

        This requires the `dustmaps <http://dustmaps.readthedocs.io>`_ package
        to run!

        Parameters
        ----------
        dustmaps_cls : ``dustmaps`` query class
            By default, ``SFDQuery``.
        """
        if dustmaps_cls is None:
            from dustmaps.sfd import SFDQuery

            dustmaps_cls = SFDQuery

        c = self.get_skycoord(distance=False)
        return dustmaps_cls().query(c)

    def get_ext(
        self, ebv: Optional[npt.ArrayLike] = None, dustmaps_cls: Optional[Any] = None
    ) -> npt.NDArray:
        """Compute the E(B-V) reddening at this location

        This requires the `dustmaps <http://dustmaps.readthedocs.io>`_ package
        to run!

        Parameters
        ----------
        dustmaps_cls : ``dustmaps`` query class
            By default, ``SFDQuery``.

        Returns
        -------
        A_G
        A_BP
        A_RP
        """
        if "ebv" not in self._cache:
            if ebv is None:
                self._cache["ebv"] = self.get_ebv(dustmaps_cls=dustmaps_cls)
            else:
                self._cache["ebv"] = ebv

        if "A_G" not in self._cache:
            A_G, A_B, A_R = get_ext_dr2_Babusiaux(
                self.phot_g_mean_mag.value,
                self.phot_bp_mean_mag.value,
                self.phot_rp_mean_mag.value,
                self._cache["ebv"],
            )

            self._cache["A_G"] = A_G * u.mag
            self._cache["A_B"] = A_B * u.mag
            self._cache["A_R"] = A_R * u.mag

        return (self._cache["A_G"], self._cache["A_B"], self._cache["A_R"])

    def get_MG(self) -> u.Quantity:
        """Return the absolute G-band magnitude."""
        return self.phot_g_mean_mag - self.distmod

    def get_G0(self, **kwargs: Any) -> u.Quantity:
        """Return the extinction-corrected G-band magnitude. Any arguments are
        passed to ``get_ext()``.
        """
        A, _, _ = self.get_ext(**kwargs)
        return self.phot_g_mean_mag - A

    def get_BP0(self, **kwargs: Any) -> u.Quantity:
        """Return the extinction-corrected G_BP magnitude. Any arguments are
        passed to ``get_ext()``."""
        _, A, _ = self.get_ext(**kwargs)
        return self.phot_bp_mean_mag - A

    def get_RP0(self, **kwargs: Any) -> u.Quantity:
        """Return the extinction-corrected G_RP magnitude. Any arguments are
        passed to ``get_ext()``."""
        _, _, A = self.get_ext(**kwargs)
        return self.phot_rp_mean_mag - A

    def get_uwe(self) -> u.Quantity:
        """Compute and return the unit-weight error."""
        return np.sqrt(self.astrometric_chi2_al / (self.astrometric_n_good_obs_al - 5))

    def get_ruwe(self) -> npt.NDArray:
        msg = (
            "Use the Gaia DR3 RUWE value instead. This can be accessed with the `.ruwe`"
            " attribute if it is present in the data table."
        )
        raise NotImplementedError(msg)

    ##########################################################################
    # Astropy connections
    #
    @property
    def skycoord(self) -> coord.SkyCoord:
        """
        Return an `~astropy.coordinates.SkyCoord` object to represent
        all coordinates. Note: this requires Astropy v3.0 or higher!

        Use the ``get_skycoord()`` method for more flexible access.
        """
        return self.get_skycoord()

    def get_skycoord(
        self,
        distance: Optional[u.Quantity[length]] = None,
        radial_velocity: Optional[u.Quantity[vel]] = None,
        ref_epoch: str = REF_EPOCH[LATEST_RELEASE],
    ) -> coord.SkyCoord:
        """
        Return an `~astropy.coordinates.SkyCoord` object to represent
        all coordinates. Note: this requires Astropy v3.0 or higher!

        `ref_epoch` is used to set the `obstime` attribute on the coordinate
        objects.  This is often included in the data release tables, but
        `ref_epoch` here is used if it's not.

        Parameters
        ----------
        distance : `~astropy.coordinate.Distance`, `~astropy.units.Quantity`, ``False``, str (optional)
            If ``None``, this inverts the parallax to get the distance from the
            Gaia data. If ``False``, distance information is ignored. If an
            astropy ``Quantity`` or ``Distance`` object, it sets the distance
            values of the output ``SkyCoord`` to whatever is passed in.
        radial_velocity : `~astropy.units.Quantity`, str (optional)
            If ``None``, this uses radial velocity data from the input Gaia
            table. If an astropy ``Quantity`` object, it sets the radial
            velocity values of the output ``SkyCoord`` to whatever is passed in.
        ref_epoch : `~astropy.time.Time`, float (optional)
            The reference epoch of the data. If not specified, this will try to
            read it from the input Gaia data table. If not provided, this will
            be set to whatever the most recent data release is, so, **beware**!

        Returns
        -------
        c : `~astropy.coordinates.SkyCoord`
            The coordinate object constructed from the input Gaia data.
        """
        _coord_opts = (distance, radial_velocity)
        if "coord" in self._cache:
            try:
                _check = self._cache["coord_opts"] == _coord_opts
            except ValueError:  # array passed for distance or radial_velocity
                _check = False

            if _check:
                return self._cache["coord"]

        kw = {}
        if self._has_rv:
            kw["radial_velocity"] = self.get_radial_velocity()

        # Reference epoch
        if "ref_epoch" in self.data.colnames:
            obstime = Time(self.ref_epoch.value, format="jyear")
        else:
            obstime = Time(ref_epoch, format="jyear")

        kw["obstime"] = obstime

        if radial_velocity is not False and radial_velocity is not None:
            if isinstance(radial_velocity, str):
                kw["radial_velocity"] = self[radial_velocity]
            else:
                kw["radial_velocity"] = radial_velocity
        elif radial_velocity is False and "radial_velocity" in kw:
            kw.pop("radial_velocity")

        if distance is None:
            kw["distance"] = self.get_distance(allow_negative=True)
        elif distance is not False and distance is not None:
            if isinstance(distance, str):
                kw["distance"] = self[distance]
            else:
                kw["distance"] = distance

        self._cache["coord"] = coord.SkyCoord(
            ra=self.ra, dec=self.dec, pm_ra_cosdec=self.pmra, pm_dec=self.pmdec, **kw
        )
        self._cache["coord_opts"] = _coord_opts

        return self._cache["coord"]

    def get_error_samples(
        self,
        size: int = 1,
        rng: Union[int, np.random.Generator, None] = None,
        **get_cov_kwargs: Any,
    ) -> "GaiaData":
        """Generate a sampling from the Gaia error distribution for each source.

        This function constructs the astrometric covariance matrix for each source and
        generates a specified number of random samples from the error distribution for
        each source. This does not handle spatially-dependent correlations. Samplings
        generated with this method can be used to, e.g., propagate the Gaia errors
        through coordinate transformations or analyses.

        Parameters
        ----------
        size : int
            The number of random samples per source to generate.
        rng : int, ``numpy.random.Generator``, optional
            The random number generator or an integer seed.

        Returns
        -------
        g_samples : `pyia.GaiaData`
            The same data table, but now each Gaia coordinate entry contains
            samples from the error distribution.

        """
        # TODO: add option to only sample in some columns - need to add names as option
        # here and in get_cov()
        if rng is None:
            rng = np.random.default_rng()
        rng = np.random.default_rng(rng)

        C, C_units = self.get_cov(**get_cov_kwargs)
        K = C.shape[1]

        # Turn missing values into 0 - but if diagonal is missing, keep note:
        nan_diag = np.where(np.isnan(C[:, np.arange(K), np.arange(K)]))
        C[np.isnan(C)] = 0.0

        arrs = []
        for k, unit in C_units.items():
            arrs.append(getattr(self, k).to_value(unit))
        y = np.stack(arrs).T

        y_samples = np.array(
            [rng.multivariate_normal(y[i], C[i], size=size) for i in range(len(y))]
        )
        y_samples[nan_diag[0], :, nan_diag[1]] = np.nan

        d = self.data.copy()
        for i, (k, unit) in enumerate(C_units.items()):
            d[k] = y_samples[..., i] * unit

        return self.__class__(d, **self._extra_kw)

    def filter(self, **kwargs: Any) -> "GaiaData":
        """
        Filter the data based on columns and data ranges.

        Parameters
        ----------
        **kwargs
            Keys should be column names, values should be tuples representing
            ranges to select the column values withing. For example, to select
            parallaxes between 0.5 and 5, pass ``parallax=(0.5, 5)*u.mas``.
            Pass `None` to skip a filter, for example ``parallax=(None,
            5*u.mas)`` would select all parallax values < 5 mas.

        Returns
        -------
        filtered_g : `pyia.GaiaData`
            The same data table, but filtered.
        """
        mask = np.ones(len(self), dtype=bool)
        for k, (x1, x2) in kwargs.items():
            if x1 is None and x2 is None:
                msg = f"Both range values are None for key {k}!"
                raise ValueError(msg)

            if x1 is None:
                mask &= self[k] < x2

            elif x2 is None:
                mask &= self[k] >= x1

            else:
                mask &= (self[k] >= x1) & (self[k] < x2)

        return self[mask]