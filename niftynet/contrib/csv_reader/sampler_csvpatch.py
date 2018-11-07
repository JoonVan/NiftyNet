# -*- coding: utf-8 -*-
"""
Generating uniformly distributed image window from input image
This can also be considered as a "random cropping" layer of the
input image.
"""
from __future__ import absolute_import, division, print_function

import numpy as np
import tensorflow as tf


from niftynet.contrib.csv_reader.sampler_csv_rows import ImageWindowDatasetCSV
from niftynet.engine.image_window import LOCATION_FORMAT
from niftynet.engine.image_window_dataset import ImageWindowDataset
from niftynet.engine.image_window import N_SPATIAL, LOCATION_FORMAT
from niftynet.io.misc_io import do_reorientation_idx


class CSVPatchSampler(ImageWindowDatasetCSV):
    """
    This class generates samples by uniformly sampling each input volume
    currently the coordinates are randomised for spatial dims only,
    i.e., the first three dims of image.

    This layer can be considered as a "random cropping" layer of the
    input image.
    """

    def __init__(self,
                 reader, csv_reader,
                 window_sizes,
                 batch_size=1,
                 windows_per_image=1,
                 queue_length=10,
                 mode_correction='remove',
                 name='csv_patchsampler_v2'):
        ImageWindowDatasetCSV.__init__(
            self,
            reader=reader,
            csv_reader=csv_reader,
            window_sizes=window_sizes,
            batch_size=batch_size,
            windows_per_image=windows_per_image,
            queue_length=queue_length,
            shuffle=True,
            epoch=-1,
            smaller_final_batch_mode='drop',
            name=name)

        tf.logging.info("initialised csv patch sampler %s ", self.window.shapes)
        self.mode_correction='remove'
        self.window_centers_sampler = rand_spatial_coordinates

    # pylint: disable=too-many-locals
    def layer_op(self, idx=None):
        """
        This function generates sampling windows to the input buffer
        image data are from ``self.reader()``

        It first completes window shapes based on image data,
        then finds random coordinates based on the window shapes
        finally extract window with the coordinates and output
        a dictionary (required by input buffer).

        :return: output data dictionary
            ``{image_modality: data_array, image_location: n_samples * 7}``
        """

        if self.window.n_samples > 1:
            raise ValueError(
            "\nThe number of windows per image has to be 1 with a csv_reader")


        csv_sampler_data = None
        flag_multi_row = False
        print("Trying to run csv patch sampler ")
        if 'sampler' not in self.csv_reader.names:
            tf.logging.warning('Uniform sampling because no csv sampler ' \
                              'provided')
        else:
            csv_sampler_data = self.csv_reader.df_by_task['sampler']

        if 'multi' in self.csv_reader.type_by_task.values():
            flag_multi_row=True

        if flag_multi_row:
            _, _, subject_id = self.csv_reader()

        print("subject id is ", subject_id)

        idx_subject_id = np.where(
            self.reader._file_list.subject_id == subject_id)[0][0]

        image_id, data, _ = self.reader(idx=idx_subject_id, shuffle=True)

        subj_indices, csv_data, _ = self.csv_reader(subject_id=subject_id)

        if 'sampler' not in self.csv_reader.names:
            print('Uniform sampling because no csv sampler provided')
        else:
            csv_sampler_data = csv_data['sampler']

        image_shapes = dict(
            (name, data[name].shape) for name in self.window.names)
        static_window_shapes = self.window.match_image_shapes(image_shapes)

        # find random coordinates based on window and image shapes
        coordinates, idx = self.csvcenter_spatial_coordinates_generator(
            subject_id=subject_id,
            data=data,
            img_sizes=image_shapes,
            win_sizes=static_window_shapes,
            n_samples=self.window.n_samples,
            mode_correction=self.mode_correction
            )

        print(idx, "index selected")
        # initialise output dict, placeholders as dictionary keys
        # this dictionary will be used in
        # enqueue operation in the form of: `feed_dict=output_dict`
        output_dict = {}
        potential_pad = self.csv_reader.pad_by_task['sampler'][idx][0]
        potential_pad_corr_end = -1.0 * \
                             np.asarray(potential_pad[N_SPATIAL:])
        potential_pad_corr = np.concatenate((potential_pad[:N_SPATIAL],
            potential_pad_corr_end),0)

        print("Pot pad is", potential_pad, potential_pad_corr,
              self.csv_reader.df_by_task['sampler'].shape)
        print( np.asarray(self.csv_reader.df_by_task['sampler'])[idx[0]])
        print(self.reader.output_list[idx_subject_id]['image']._output_axcodes,
              self.reader.output_list[idx_subject_id]['label'].original_axcodes)

        samples=idx[0]+np.arange(-2,4)
        print(self.csv_reader.pad_by_task['sampler'][samples])
        # fill output dict with data
        for name in list(data):
            coordinates_key = LOCATION_FORMAT.format(name)
            image_data_key = name

            # fill the coordinates
            location_array = coordinates[name]
            output_dict[coordinates_key] = location_array

            # fill output window array
            image_array = []
            for window_id in range(self.window.n_samples):
                x_start, y_start, z_start, x_end, y_end, z_end = \
                    location_array[window_id, 1:].astype(np.int32) + \
                    potential_pad_corr.astype(np.int32)
                print(location_array[window_id,1:]+potential_pad_corr)
                try:
                    image_window = data[name][
                                   x_start:x_end, y_start:y_end,
                                   z_start:z_end, ...]
                    if np.sum(potential_pad) > 0:
                        new_pad = np.reshape(potential_pad,[2, N_SPATIAL]).T
                        add_pad = np.tile([0,0],[len(np.shape(
                            image_window))-N_SPATIAL, 1])
                        new_pad = np.concatenate((new_pad, add_pad), 0)
                        new_img = np.pad(image_window, pad_width=new_pad,
                                         mode='constant',
                                         constant_values=0)
                        image_array.append(new_img[np.newaxis,...])
                    else:
                        image_array.append(image_window[np.newaxis, ...])
                except ValueError:
                    tf.logging.fatal(
                        "dimensionality miss match in input volumes, "
                        "please specify spatial_window_size with a "
                        "3D tuple and make sure each element is "
                        "smaller than the image length in each dim. "
                        "Current coords %s", location_array[window_id])
                    raise
            if len(image_array) > 1:
                output_dict[image_data_key] = \
                    np.concatenate(image_array, axis=0)
            else:
                output_dict[image_data_key] = image_array[0]
        # fill output dict with csv_data
        if self.csv_reader is not None:
            idx_dict = {}
            list_keys = self.csv_reader.df_by_task.keys()
            for k in list_keys:
                if self.csv_reader.type_by_task[k] == 'multi':
                    idx_dict[k] = idx
                else:
                    for n in range(0, self.window.n_samples):
                        idx_dict[k] = 0
            _, csv_data_dict,_ = self.csv_reader(idx=idx_dict,
                                                 subject_id=subject_id)
            for name in csv_data_dict.keys():
                csv_data_array = []
                for n in range(0, self.window.n_samples):
                    csv_data_array.append(csv_data_dict[name])
                if len(csv_data_array) == 1:
                    output_dict[name] = np.asarray(csv_data_array[0],
                                                   dtype=np.float32)
                else:
                    output_dict[name] = np.concatenate(
                        csv_data_array, 0).astype(dtype=np.float32)
                # if name != 'sampler':
                #     csv_data_array = []
                #     for n in range(0, self.window.n_samples):
                #         csv_data_array.append(csv_data_dict[name])
                #     if len(csv_data_array) == 1:
                #         output_dict[name] = np.asarray(csv_data_array[0],
                #                                        dtype=np.float32)
                #     else:
                #         output_dict[name] = np.concatenate(
                #             csv_data_array,0).astype(dtype=np.float32)
                #
                # else:
                #     csv_data_array=[]
                #     for n in range(0, self.window.n_samples):
                #         csv_data_array.append(csv_data_dict['sampler'])
                #     if len(csv_data_array) == 1:
                #         # Need to cast to float
                #         output_dict['sampler'] = np.asarray(csv_data_array[0],
                #                                        dtype=np.float32)
                #     else:
                #         output_dict['sampler'] = np.concatenate(
                #             csv_data_array,0).astype(np.float32)
            # _, label_dict, _ = self.csv_reader(subject_id=image_id)
            # for name in self.csv_reader.task_param.keys():
            #
            # output_dict.update(label_dict)
            for name in csv_data_dict.keys():
                output_dict[name + '_location'] = output_dict['image_location']
        return output_dict
        # the output image shape should be
        # [enqueue_batch_size, x, y, z, time, modality]
        # where enqueue_batch_size = windows_per_image


    def csvcenter_spatial_coordinates_generator(self,
                                       subject_id,
                                       data,
                                       img_sizes,
                                       win_sizes,
                                       mode_correction='remove',
                                       n_samples=1):
        """
        Generate spatial coordinates for sampling.

        Values in ``win_sizes`` could be different --
        for example in a segmentation network ``win_sizes`` could be
        ``{'training_image_spatial_window': (32, 32, 10),
           'Manual_label_spatial_window': (16, 16, 10)}``
        (the network reduces x-y plane spatial resolution).

        This function handles this situation by first find the largest
        window across these window definitions, and generate the coordinates.
        These coordinates are then adjusted for each of the
        smaller window sizes (the output windows are almost concentric).
        """

        assert data is not None, "No input from image reader. Please check" \
                                 "the configuration file."

        # infer the largest spatial window size and check image spatial shapes
        img_spatial_size, win_spatial_size = \
            _infer_spatial_size(img_sizes, win_sizes)

        window_centres = None
        reject=False
        if mode_correction == 'remove':
            reject=True

        # try:
        #     window_centres = csv_data.get('sampler', None)
        # except AttributeError:
        #     pass

        n_samples = max(n_samples, 1)
        all_coordinates = {}
        pb_coordinates = {}
        list_idx = {}
        if 'sampler' not in self.csv_reader.task_param.keys():
            window_centres = rand_spatial_coordinates(n_samples,
                                                            img_spatial_size,
                                                            win_spatial_size,
                                                            None)
            list_idx = np.arange(0, n_samples)

        else :
            window_centres_list = []
            list_idx = []
            list_mod = list(img_sizes.keys())
            print(list_mod)
            idx_subject_id = np.where(
                self.reader._file_list.subject_id == subject_id)[0][0]
            output_shape = self.reader.output_list[idx_subject_id][list_mod[
                0]].shape[:N_SPATIAL]
            self.check_csv_sampler_valid(subject_id, img_sizes, win_sizes)
            for mod in self.csv_reader.task_param:
                all_coordinates[mod] = []
            for n in range(0, n_samples):
                idx, data_csv, _ = self.csv_reader(
                    subject_id=subject_id,  mode='single', reject=reject)

                centre_transform = self.transform_centres(subject_id, img_sizes,
                                                          np.expand_dims(
                                                              np.squeeze(
                                                              data_csv[
                                                              'sampler']),0))
                # centre_tmp = np.expand_dims(centre_transform,0)
                for mod in idx.keys():
                    all_coordinates[mod].append(np.expand_dims(
                        np.squeeze(data_csv[mod]),0))
                list_idx.append(idx['sampler'])
                print(centre_transform.shape)
                window_centres_list.append(centre_transform)
                window_centres = np.concatenate(window_centres_list, 0)
        print("all prepared and added ")

        assert window_centres.shape == (n_samples, N_SPATIAL), \
            "the coordinates generator should return " \
            "{} samples of rank {} locations".format(n_samples, N_SPATIAL)

        # adjust spatial coordinates based on each mod spatial window size

        for mod in list(win_sizes):
            win_size = np.asarray(win_sizes[mod][:N_SPATIAL])
            half_win = np.floor(win_size / 2.0).astype(int)

            # Make starting coordinates of the window
            spatial_coords = np.zeros(
                (1, N_SPATIAL * 2), dtype=np.int32)
            spatial_coords[:, :N_SPATIAL] = np.maximum(
                window_centres[0, :N_SPATIAL] - half_win[:N_SPATIAL], 0)
            sign_pb_min = (np.square(np.sign(window_centres[0,
                                          :N_SPATIAL]-half_win[
                                                             :N_SPATIAL])) - \
                      np.sign(window_centres[0, :N_SPATIAL] - half_win[
                                                              :N_SPATIAL]))/2

            # Make the opposite corner of the window is
            # just adding the mod specific window size
            spatial_coords[:, N_SPATIAL:] = \
                spatial_coords[:, :N_SPATIAL] + win_size[:N_SPATIAL]
            sign_pb_max = np.sign(spatial_coords[:, N_SPATIAL:] - img_spatial_size)
            pb_coordinates_tmp = np.where(sign_pb_max == 1, np.ones_like(
                sign_pb_max, dtype=np.float64), np.zeros_like(sign_pb_max,
                                                           dtype=np.float64))
            pb_coordinates_tmp += sign_pb_min
            needed_correction_min = window_centres[0,
                                          :N_SPATIAL]-half_win[
                                                             :N_SPATIAL]
            needed_correction_min = np.expand_dims(np.where(
                needed_correction_min<0,
                                             -1 * needed_correction_min,
                                             np.zeros_like(
                                                 needed_correction_min)),0)
            needed_correction_max = spatial_coords[:, N_SPATIAL:] - img_spatial_size
            needed_correction_max = np.where(needed_correction_max>0,
                                             needed_correction_max,
                                             np.zeros_like(
                                                 needed_correction_max))
            needed_correction_full = np.concatenate((needed_correction_min,
                                                     needed_correction_max),
                                                    axis=1)


            # assert np.all(spatial_coords[:, N_SPATIAL:] <= img_spatial_size), \
            #     'spatial coords: out of bounds.'

            # include subject id as the 1st column of all_coordinates values
            idx_subject_id = np.where(
                self.reader._file_list.subject_id == subject_id)[0][0]
            idx_subject_id = np.ones((n_samples,), dtype=np.int32) * \
                             idx_subject_id
            spatial_coords = np.append(
                idx_subject_id[:, None], spatial_coords, axis=1)
            all_coordinates[mod] = spatial_coords
            pb_coordinates[mod] = needed_correction_full

        return all_coordinates, list_idx

    def transform_centres(self, subject_id, img_sizes, windows_centres):
        # For the moment assuming that same img size and orientations across
        # modalities
        list_mod = list(img_sizes.keys())
        print(list_mod)
        idx_subject_id = np.where(
            self.reader._file_list.subject_id == subject_id)[0][0]
        output_shape = self.reader.output_list[idx_subject_id][list_mod[
            0]].shape[:N_SPATIAL]
        init_axcodes = self.reader.output_list[idx_subject_id][list_mod[
            0]].original_axcodes

        fin_axcodes = self.reader.output_list[idx_subject_id][
            list_mod[0]].output_axcodes
        print(output_shape, init_axcodes[0], fin_axcodes[0])
        transformed_centres = do_reorientation_idx(windows_centres,
                                                   init_axcodes[0],
                                                   fin_axcodes[0],
                                                   output_shape)
        padding = (np.asarray(img_sizes[list_mod[0]][:N_SPATIAL]) -
                   np.asarray(output_shape)) / 2.0
        transformed_centres = np.squeeze(transformed_centres.astype(np.int32))
        if transformed_centres.ndim==1:
            transformed_centres = np.expand_dims(transformed_centres,0)
        padding = padding.astype(np.int32)
        print(transformed_centres.shape, padding.shape)
        transformed_centres += np.tile(np.expand_dims(padding,0),
                                       [len(windows_centres),
                                                        1],
                                       )
        return transformed_centres

    def check_csv_sampler_valid(self, subject_id, img_sizes, win_sizes):
        print("Checking if csv_sampler valid is updated")
        idx_multi, csv_data, _ = self.csv_reader(subject_id=subject_id,
                                                 idx=None,
                                    mode='multi')

        windows_centres = csv_data['sampler']
        print("Windows extracted")
        transformed_centres = self.transform_centres(subject_id, img_sizes,
                                                     windows_centres)




        checked = self.csv_reader.valid_by_task['sampler'][idx_multi['sampler']]
        pad = self.csv_reader.pad_by_task['sampler'][idx_multi['sampler']]

        numb = windows_centres.shape[0]
        img_spatial_size, win_spatial_size = \
            _infer_spatial_size(img_sizes, win_sizes)
        if np.min(checked) >= 0:
            return
        else:
            tf.logging.warning("Need to checked validity of samples for "
                               "subject %s" %subject_id)
            checked = np.ones([numb])
            pad = np.zeros([numb, 2*N_SPATIAL])
            for mod in list(win_sizes):
                print("mod is %s" %mod)
                print(img_spatial_size)
                win_size = np.asarray(win_sizes[mod][:N_SPATIAL])
                half_win = np.floor(win_size / 2.0).astype(int)

                # Make starting coordinates of the window
                spatial_coords = np.zeros(
                    (numb, N_SPATIAL * 2), dtype=np.int32)
                half_win_tiled = np.tile(half_win[:N_SPATIAL],[numb,1])
                reshaped_windows = np.reshape(transformed_centres[:,:N_SPATIAL],
                                              half_win_tiled.shape)
                spatial_coords[:, :N_SPATIAL] = reshaped_windows- half_win_tiled

                min_spatial_coords = np.max(-1*spatial_coords, 1)
                checked = np.asarray(np.where(min_spatial_coords>0,
                                             np.zeros_like(
                    checked), checked))
                pad = np.maximum(-1*spatial_coords, pad )
                # Make the opposite corner of the window is
                # just adding the mod specific window size
                spatial_coords[:, N_SPATIAL:] = spatial_coords[:, :N_SPATIAL] + np.tile(win_size[
                                                            :N_SPATIAL],
                                                            [numb,1])

                max_spatial_coords = np.max(spatial_coords[:,N_SPATIAL:] -
                                            np.tile(img_spatial_size,[numb,
                                                                      1]),
                                            axis=1)
                diff_spatial_size = spatial_coords[:, N_SPATIAL:] - np.tile(
                    img_spatial_size, [numb, 1])
                checked = np.asarray(np.where(max_spatial_coords>0,
                                             np.zeros_like(
                    checked), checked))
                pad[:, N_SPATIAL:] = np.maximum(diff_spatial_size, pad[:,
                                                                   N_SPATIAL:])

                tf.logging.warning("to discard or pad is %d out of %d for mod "
                                   "%s" % (
                    numb-np.sum(checked), numb, mod))

            print("check on idx_multi", np.asarray(idx_multi['sampler']).shape,
                                                  np.asarray(idx_multi[
                                                               'sampler']).dtype, checked.dtype)
            idx_discarded = []
            for i in range(0, len(checked)):
                self.csv_reader.valid_by_task['sampler'][idx_multi[
                    'sampler'][i]] = checked[i]
                self.csv_reader.pad_by_task['sampler'][idx_multi['sampler'][
                    i]] = pad[i]
                if checked[i] == 0:
                    idx_discarded.append(idx_multi['sampler'][i])
            # self.csv_reader.valid_by_task['sampler'][np.asarray(idx_multi[
            #     'sampler'])] = checked
            print('Updated check')
            if np.sum(checked) < numb:
                tf.logging.warning("The following indices are not valid for "
                                   "%s %s " %(
                                   subject_id, ' '.join(map(str,
                                                            idx_discarded))))
            print(
                "updated valid part of csv_reader for subject %s" % subject_id)
            return


def correction_coordinates(coordinates, idx, pb_coord, img_sizes, win_sizes,
                           csv_sampler, mode="remove"):
    # infer the largest spatial window size and check image spatial shapes

    img_spatial_size, win_spatial_size =_infer_spatial_size(img_sizes, win_sizes)
    overall_pb = np.zeros([len(idx), 1])
    numb_wind = len(idx)
    for mod in list(win_sizes):
        overall_pb += np.sum(np.abs(pb_coord[mod]), 1)
    if np.sum(overall_pb) == 0:
        return coordinates, None
    else:

        list_nopb = np.where(overall_pb == 0)
        list_pb = np.where(overall_pb > 0)
        idx_pb = idx[list_pb]
        if mode == "remove":
            for mod in list(win_sizes):
                coordinates[mod]=coordinates[mod][list_nopb, :]
            return coordinates, idx_pb
        elif mode == "replace" :
            n_pb = np.sum(overall_pb)
            window_centres_replacement = rand_spatial_coordinates(n_pb,
                                                      img_spatial_size,
                                                      win_spatial_size,
                                                      None)
            spatial_coords_replacement = np.zeros(
                (n_pb, N_SPATIAL * 2), dtype=np.int32)

            for mod in list(win_sizes):
                win_size = np.asarray(win_sizes[mod][:N_SPATIAL])
                half_win = np.floor(win_size / 2.0).astype(int)

                # Make starting coordinates of the window
                spatial_coords_replacement[:, :N_SPATIAL] = np.maximum(
                    window_centres_replacement[:, :N_SPATIAL] - np.tile(
                        half_win[:N_SPATIAL], [n_pb, 1]), 0)
                spatial_coords_replacement[:, N_SPATIAL:] = \
                    spatial_coords_replacement[:, :N_SPATIAL] + np.tile(
                        win_size[:N_SPATIAL], [n_pb, 1])
            n_replaced = 0
            for n in range(0, numb_wind):
                if overall_pb[n]:
                    coordinates[n, :] = spatial_coords_replacement[n_replaced]
                    n_replaced += 1
            return coordinates, idx_pb


def rand_spatial_coordinates(
        n_samples, img_spatial_size, win_spatial_size, sampler_map):
    """
    Generate spatial coordinates from a discrete uniform distribution.

    :param n_samples: number of random coordinates to generate
    :param img_spatial_size: input image size
    :param win_spatial_size: input window size
    :param sampler_map: sampling prior map (not in use)
    :return: (n_samples, N_SPATIAL) coordinates representing sampling
              window centres relative to img_spatial_size
    """
    tf.logging.debug('uniform sampler, prior %s ignored', sampler_map)

    # Sample coordinates at random
    half_win = np.floor(np.asarray(win_spatial_size) / 2.0).astype(np.int32)
    max_coords = np.zeros((n_samples, N_SPATIAL), dtype=np.int32)
    for (idx, (img, win)) in enumerate(
            zip(img_spatial_size[:N_SPATIAL], win_spatial_size[:N_SPATIAL])):
        max_coords[:, idx] = np.random.randint(
            0, max(img - win + 1, 1), n_samples)
    max_coords[:, :N_SPATIAL] = \
        max_coords[:, :N_SPATIAL] + half_win[:N_SPATIAL]
    return max_coords


def _infer_spatial_size(img_sizes, win_sizes):
    """
    Utility function to find the spatial size of image,
    and the largest spatial window size across input sections.

    Raises NotImplementedError if the images have
    different spatial dimensions.

    :param img_sizes: dictionary of {'input_name': (img_size_x, img_size,y,...)}
    :param win_sizes: dictionary of {'input_name': (win_size_x, win_size_y,...)}
    :return: (image_spatial_size, window_largest_spatial_size)
    """
    uniq_spatial_size = \
        set([img_size[:N_SPATIAL] for img_size in list(img_sizes.values())])
    if len(uniq_spatial_size) != 1:
        tf.logging.fatal("Don't know how to generate sampling "
                         "locations: Spatial dimensions of the "
                         "grouped input sources are not "
                         "consistent. %s", uniq_spatial_size)
        raise NotImplementedError
    img_spatial_size = np.asarray(uniq_spatial_size.pop(), dtype=np.int32)

    # find the largest spatial window across input sections
    _win_spatial_sizes = \
        [win_size[:N_SPATIAL] for win_size in win_sizes.values()]
    _win_spatial_sizes = np.asarray(_win_spatial_sizes, dtype=np.int32)
    win_spatial_size = np.max(_win_spatial_sizes, axis=0)

    assert all([img_spatial_size[i] >= win_spatial_size[i]
                for i in range(N_SPATIAL)]), \
        "window size {} is larger than image size {}".format(
            win_spatial_size, img_spatial_size)

    return img_spatial_size, win_spatial_size