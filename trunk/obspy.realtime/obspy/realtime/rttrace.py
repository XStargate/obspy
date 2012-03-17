# -*- coding: utf-8 -*-
"""
Module for handling ObsPy RtTrace objects.

:copyright:
    The ObsPy Development Team (devs@obspy.org) & Anthony Lomax
:license:
    GNU Lesser General Public License, Version 3
    (http://www.gnu.org/copyleft/lesser.html)
"""

from obspy.core import Trace, Stats
from obspy.realtime.rtmemory import RtMemory
from obspy.realtime import signal
import numpy as np


# dictionary to map given type-strings to processing functions keys must be all
# lower case - values are tuples: (function name, number of RtMemory objects)
REALTIME_PROCESS_FUNCTIONS = {
    'scale': (signal.scale, 0),
    'integrate': (signal.integrate, 1),
    'differentiate': (signal.differentiate, 1),
    'boxcar': (signal.boxcar, 1),
    'tauc': (signal.tauc, 2),
    'mwpintegral': (signal.mwpIntegral, 1),
}


class RtTrace(Trace):
    """
    An object containing data of a continuous series constructed dynamically
    from sequential data packets.

    New data packets may be periodically appended. Registered time-domain
    processes can be applied to the new data and the resulting trace will be
    left trimmed to maintain a specified maximum trace length.

    :type max_length: int, optional
    :param max_length: maximum trace length in seconds

    .. rubric:: Example

    RtTrace has been built to handle real time processing of periodically
    append data packets, such as adding and processing data requested from an
    SeedLink server. See :mod:`obspy.seedlink` for further information.

    For the sake of simplicity we will just split data of an existing example
    file into multiple chucks (Trace objects) of about equal size (step 1 + 2)
    and append those chunks in a simple loop (step 4) into an RtTrace object.
    Additionally there are two real time processing functions registered to the
    RtTrace object (step 3) which will automatically process any appended data
    chunks.

    1. Read first trace of example SAC data file and extract contained time
       offset and epicentral distance of an earthquake::

        >>> import numpy as np
        >>> from obspy.realtime import RtTrace, _splitTrace
        >>> from obspy.core import read
        >>> from obspy.realtime.signal import calculateMwpMag
        >>> data_trace = read('/path/to/II.TLY.BHZ.SAC')[0]
        >>> len(data_trace)
        12684
        >>> ref_time_offset = data_trace.stats.sac.a
        >>> print(ref_time_offset)
        301.506
        >>> epicentral_distance = data_trace.stats.sac.gcarc
        >>> print(epicentral_distance)
        30.0855

    2. Split given trace into a list of three sub-traces::

        >>> traces = _splitTrace(data_trace, num=3)
        >>> [len(tr) for tr in traces]
        [4228, 4228, 4228]

    3. Assemble real time trace and register two processes::

        >>> rt_trace = RtTrace()
        >>> rt_trace.registerRtProcess('integrate')
        1
        >>> rt_trace.registerRtProcess('mwpIntegral', mem_time=240,
        ...     ref_time=(data_trace.stats.starttime + ref_time_offset),
        ...     max_time=120, gain=1.610210e+09)
        2

    4. Append and auto-process packet data into RtTrace::

        >>> for tr in traces:
        ...     processed_trace = rt_trace.append(tr, gap_overlap_check=True)
        ...
        >>> len(rt_trace)
        12684

    5. Some post processing to get Mwp::

        >>> peak = np.amax(np.abs(rt_trace.data))
        >>> print(peak)
        0.136404
        >>> mwp = calculateMwpMag(peak, epicentral_distance)
        >>> print(mwp)
        8.78902911791
    """
    max_length = None
    have_appended_data = False

    @classmethod
    def rtProcessFunctionsToString(cls):
        """
        Return doc string for all predefined real-time processing functions.

        :rtype: str
        :return: String containing doc for all real-time processing functions.
        """
        string = 'Real-time processing functions (use as: ' + \
            'RtTrace.registerRtProcess(process_name, [parameter values])):\n'
        for key in REALTIME_PROCESS_FUNCTIONS:
            string += '\n'
            string += '  ' + (str(key) + ' ' + 80 * '-')[:80]
            string += str(REALTIME_PROCESS_FUNCTIONS[key][0].__doc__)
        return(string)

    def __init__(self, max_length=None, *args, **kwargs):  # @UnusedVariable
        """
        Initializes an RtTrace.

        See :class:`obspy.core.trace.Trace` for all parameters.
        """
        # set window length attribute
        if max_length is not None and max_length <= 0:
            raise ValueError("Input max_length out of bounds: %s" % max_length)
        self.max_length = max_length

        # initialize processing list
        self.processing = []

        # initialize parent Trace with no data or header - all data must be
        # added using append
        super(RtTrace, self).__init__(data=np.array([]), header=None)

    def __eq__(self, other):
        """
        Implements rich comparison of RtTrace objects for "==" operator.

        Traces are the same, if both their data and stats are the same.
        """
        # check if other object is a RtTrace
        if not isinstance(other, RtTrace):
            return False
        return super(RtTrace, self).__eq__(other)

    def __add__(self, **kwargs):  # @UnusedVariable
        """
        Too ambiguous, throw an Error.

        .. seealso:: :meth:`obspy.realtime.RtTrace.append`.
        """
        msg = "Too ambiguous for realtime trace data. Try: RtTrace.append()"
        raise NotImplementedError(msg)

    def append(self, trace, gap_overlap_check=False, verbose=False):
        """
        Appends a Trace object to this RtTrace.

        Registered real-time processing will be applied to appended Trace
        object before it is appended.  This RtTrace will be truncated from
        the beginning to RtTrace.max_length, if specified.
        Sampling rate, data type and trace.id of both traces must match.

        :type trace: :class:`~obspy.core.trace.Trace`
        :param trace:  :class:`~obspy.core.trace.Trace` object to append to
            this RtTrace
        :type gap_overlap_check: bool, optional
        :param gap_overlap_check: Action to take when there is a gap or overlap
            between the end of this RtTrace and start of appended Trace:
                If True, raise TypeError.
                If False, all trace processing memory will be re-initialized to
                    prevent false signal in processed trace.
            (default is ``True``).
        :type verbose: bool, optional
        :param verbose: Print additional information to stdout
        :return: NumPy :class:`np.ndarray` object containing processed trace
            data from appended Trace object.
        """
        # make sure datatype is compatible with Trace.__add__() which returns
        # array of float32 - convert f4 datatype to float32
        if trace.data.dtype == '>f4' or trace.data.dtype == '<f4':
            trace.data = np.array(trace.data, dtype=np.float32)

        # sanity checks
        if self.have_appended_data:
            if not isinstance(trace, Trace):
                raise TypeError
            #  check id
            if self.getId() != trace.getId():
                raise TypeError("Trace ID differs:", self.getId(),
                                trace.getId())
            #  check sample rate
            if self.stats.sampling_rate != trace.stats.sampling_rate:
                raise TypeError("Sampling rate differs:",
                                self.stats.sampling_rate,
                                trace.stats.sampling_rate)
            #  check calibration factor
            if self.stats.calib != trace.stats.calib:
                raise TypeError("Calibration factor differs:",
                                self.stats.calib, trace.stats.calib)
            # check data type
            if self.data.dtype != trace.data.dtype:
                raise TypeError("Data type differs:",
                                self.data.dtype, trace.data.dtype)
        # TODO: IMPORTANT? Should improve check for gaps and overlaps
        # and handle more elegantly
        # check times
        gap_or_overlap = False
        if self.have_appended_data:
            #if self.stats.starttime <= trace.stats.starttime:
            #    lt = self
            #    rt = trace
            #else:
            #    rt = self
            #    lt = trace
            sr = self.stats.sampling_rate
            #delta = int(math.floor(\
            #    round((rt.stats.starttime - lt.stats.endtime) * sr, 5) )) - 1
            diff = trace.stats.starttime - self.stats.endtime
            delta = diff * sr - 1.0
            if verbose:
                msg = "%s: Overlap/gap of (%g) samples in data: (%s) (%s) " + \
                    "diff=%gs  dt=%gs"
                print  msg % (self.__class__.__name__,
                              delta, self.stats.endtime, trace.stats.starttime,
                              diff, 1.0 / sr)
            if delta < -0.1:
                msg = self.__class__.__name__ + ": " \
                "Overlap of (%g) samples in data: (%s) (%s) diff=%gs  dt=%gs" \
                    % (-delta, self.stats.endtime, trace.stats.starttime, \
                       diff, 1.0 / sr)
                if gap_overlap_check:
                    raise TypeError(msg)
                gap_or_overlap = True
            if delta > 0.1:
                msg = self.__class__.__name__ + ": " \
                "Gap of (%g) samples in data: (%s) (%s) diff=%gs  dt=%gs" \
                    % (delta, self.stats.endtime, trace.stats.starttime, \
                       diff, 1.0 / sr)
                if gap_overlap_check:
                    raise TypeError(msg)
                gap_or_overlap = True
            if gap_or_overlap:
                print "Warning: " + msg
                print "   Trace processing memory will be re-initialized."
            else:
                # correct start time to pin absolute trace timing to start of
                # appended trace, this prevents slow drift of nominal trace
                # timing from absolute time when nominal sample rate differs
                # from true sample rate
                self.stats.starttime = self.stats.starttime + diff - 1.0 / sr
                if verbose:
                    print "%s: self.stats.starttime adjusted by: %gs" \
                    % (self.__class__.__name__, diff - 1.0 / sr)

        # first apply all registered processing to Trace
        for proc in self.processing:
            #print 'DEBUG: Applying processing: ', proc
            process_name, options, rtmemory_list = proc
            # if gap or overlap, clear memory
            if gap_or_overlap and rtmemory_list != None:
                for n in range(len(rtmemory_list)):
                    rtmemory_list[n] = RtMemory()
            #print 'DEBUG: Applying processing: ', process_name, ' ', options
            # apply processing
            if hasattr(process_name, '__call__'):
                # check if direct function call
                trace.data = process_name(trace.data, **options)
            else:
                # got predefined function
                func = REALTIME_PROCESS_FUNCTIONS[process_name.lower()][0]
                trace.data = func(trace, rtmemory_list, **options)

        # if first data, set stats
        if not self.have_appended_data:
            self.data = np.array(trace.data)
            self.stats = Stats(header=trace.stats)
        else:
            # fix Trace.__add__ parameters
            # TODO: IMPORTANT? Should check for gaps and overlaps and handle
            # more elegantly
            method = 0
            interpolation_samples = 0
            fill_value = 'latest'
            sanity_checks = True
            #print "DEBUG: ->trace.stats.endtime:", trace.stats.endtime
            sum_trace = Trace.__add__(self, trace, method,
                                      interpolation_samples,
                                      fill_value, sanity_checks)
            # Trace.__add__ returns new Trace, so update to this RtTrace
            self.data = sum_trace.data
            # set derived values, including endtime
            self.stats.__setitem__('npts', sum_trace.stats.npts)
            #print "DEBUG: add->self.stats.endtime:", self.stats.endtime

            # left trim if data length exceeds max_length
            #print "DEBUG: max_length:", self.max_length
            if self.max_length != None:
                max_samples = int(self.max_length * \
                                  self.stats.sampling_rate + 0.5)
                #print "DEBUG: max_samples:", max_samples,
                #    " np.size(self.data):", np.size(self.data)
                if np.size(self.data) > max_samples:
                    starttime = self.stats.starttime \
                        + (np.size(self.data) - max_samples) \
                        / self.stats.sampling_rate
                    # print "DEBUG: self.stats.starttime:",
                    #     self.stats.starttime, " new starttime:", starttime
                    self._ltrim(starttime, pad=False, nearest_sample=True,
                                fill_value=None)
                    #print "DEBUG: self.stats.starttime:",
                    #     self.stats.starttime, " np.size(self.data):",
                    #     np.size(self.data)
        self.have_appended_data = True
        return(trace)

    def registerRtProcess(self, process, **options):
        """
        Adds real-time processing algorithm to processing list of this RtTrace.

        Processing function must be one of:
            %s. % REALTIME_PROCESS_FUNCTIONS.keys()
            or a non-recursive, time-domain np or obspy function which takes
            a single array as an argument and returns an array

        :type process: str or function
        :param process: Specifies which processing function is added,
            e.g. ``"boxcar"`` or ``np.abs``` (functions without brackets).
            See :mod:`obspy.realtime.signal` for all predefined processing
            functions.
        :type options: dict, optional
        :param options: Required keyword arguments to be passed the respective
            processing function, e.g. ``width=100`` for ``'boxcar'`` process.
            See :mod:`obspy.realtime.signal` for all options.
        :rtype: int
        :return: Length of processing list after registering new processing
            function.
        """
        # create process_name either from string or function name
        process_name = ("%s" % process).lower()

        # set processing entry for this process
        entry = False
        rtmemory_list = None
        if hasattr(process, '__call__'):
            # direct function call
            entry = (process, options, None)
        elif process_name in REALTIME_PROCESS_FUNCTIONS:
            # predefined function
            num = REALTIME_PROCESS_FUNCTIONS[process_name][1]
            if num:
                rtmemory_list = [RtMemory()] * num
            entry = (process_name, options, rtmemory_list)
        else:
            # check if process name is contained within a predefined function,
            # e.g. 'int' for 'integrate'
            for key in REALTIME_PROCESS_FUNCTIONS:
                if not key.startswith(process_name):
                    continue
                process_name = key
                num = REALTIME_PROCESS_FUNCTIONS[process_name][1]
                if num:
                    rtmemory_list = [RtMemory()] * num
                entry = (process_name, options, rtmemory_list)
                break

        if not entry:
            raise NotImplementedError("Can't register process %s" % (process))

        # add process entry
        self.processing.append(entry)

        # add processing information to the stats dictionary
        proc_info = "realtime_process:%s:%s" % (process_name, options)
        self._addProcessingInfo(proc_info)

        return len(self.processing)


def _splitTrace(trace, num=3):
    """
    Helper functions to split given Trace into num Traces of the same size.

    :type trace: :class:`obspy.core.trace.Trace`
    :param trace: ObsPy Trace object.
    :type num: int
    :param num: number of returned traces, default to ``3``.
    :return: list of traces.

    .. rubric:: Example

    >>> from obspy.core import read, Stream
    >>> original_trace = read()[0]
    >>> print(original_trace)  # doctest: +ELLIPSIS
    BW.RJOB..EHZ | 2009-08-24T00:20:03.000000Z - ... | 100.0 Hz, 3000 samples
    >>> len(original_trace)
    3000
    >>> traces = _splitTrace(original_trace, 7)
    >>> [len(tr) for tr in traces]
    [429, 429, 429, 429, 429, 429, 426]
    >>> st = Stream(traces)
    >>> print(st)  # doctest: +ELLIPSIS
    7 Trace(s) in Stream:
    BW.RJOB..EHZ | 2009-08-24T00:20:03.000000Z - ... | 100.0 Hz, 429 samples
    BW.RJOB..EHZ | 2009-08-24T00:20:07.290000Z - ... | 100.0 Hz, 429 samples
    BW.RJOB..EHZ | 2009-08-24T00:20:11.580000Z - ... | 100.0 Hz, 429 samples
    BW.RJOB..EHZ | 2009-08-24T00:20:15.870000Z - ... | 100.0 Hz, 429 samples
    BW.RJOB..EHZ | 2009-08-24T00:20:20.160000Z - ... | 100.0 Hz, 429 samples
    BW.RJOB..EHZ | 2009-08-24T00:20:24.450000Z - ... | 100.0 Hz, 429 samples
    BW.RJOB..EHZ | 2009-08-24T00:20:28.740000Z - ... | 100.0 Hz, 426 samples
    >>> st.merge(-1)
    >>> st[0] == original_trace
    True
    """
    total_length = np.size(trace.data)
    rest_length = total_length % num
    if rest_length:
        packet_length = (total_length // num)
    else:
        packet_length = (total_length // num) - 1
    tstart = trace.stats.starttime
    tend = tstart + (trace.stats.delta * packet_length)
    traces = []
    for _i in range(num):
        traces.append(trace.slice(tstart, tend))
        tstart = tend + trace.stats.delta
        tend = tstart + (trace.stats.delta * packet_length)
    return traces


if __name__ == '__main__':
    import doctest
    doctest.testmod(exclude_empty=True)