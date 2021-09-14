from scipy import signal

from module.base.timer import Timer
from module.base.utils import *
from module.combat.assets import *
from module.commission.assets import *
from module.commission.project import Commission, COMMISSION_FILTER, SHORTEST_FILTER
from module.exception import GameStuckError
from module.handler.info_handler import InfoHandler
from module.logger import logger
from module.map.map_grids import SelectedGrids
from module.ui.page import page_reward, page_commission
from module.ui.scroll import Scroll
from module.ui.switch import Switch
from module.ui.ui import UI

COMMISSION_SWITCH = Switch('Commission_switch', is_selector=True)
COMMISSION_SWITCH.add_status('daily', COMMISSION_DAILY)
COMMISSION_SWITCH.add_status('urgent', COMMISSION_URGENT)
COMMISSION_SCROLL = Scroll(COMMISSION_SCROLL_AREA, color=(247, 211, 66), name='COMMISSION_SCROLL')


class RewardCommission(UI, InfoHandler):
    daily: SelectedGrids
    urgent: SelectedGrids
    daily_choose: SelectedGrids
    urgent_choose: SelectedGrids
    max_commission = 4

    def _commission_detect(self, image):
        """
        Get all commissions from an image.

        Args:
            image: Pillow image

        Returns:
            SelectedGrids:
        """
        commission = []
        # Find white lines under each commission to locate them.
        # (597, 0, 619, 720) is somewhere with white lines only.
        color_height = np.mean(image.crop((597, 0, 619, 720)).convert('L'), axis=1)
        parameters = {'height': 200, 'distance': 100}
        peaks, _ = signal.find_peaks(color_height, **parameters)
        # 67 is the height of commission list header
        # 117 is the height of one commission card.
        peaks = [y for y in peaks if y > 67 + 117]

        # Add commission to list
        for y in peaks:
            comm = Commission(image, y=y, config=self.config)
            logger.attr('Commission', comm)
            commission.append(comm)

        return SelectedGrids(commission)

    def _commission_choose(self, daily, urgent):
        """
        Args:
            daily (SelectedGrids):
            urgent (SelectedGrids):

        Returns:
            SelectedGrids, SelectedGrids: Chosen daily commission, Chosen urgent commission
        """
        # Count Commission
        total = daily.add_by_eq(urgent)
        self.max_commission = 4
        for comm in total:
            if comm.genre == 'event_daily':
                self.max_commission = 5
        running_count = int(
            np.sum([1 for c in total if c.status == 'running']))
        logger.attr('Running', f'{running_count}/{self.max_commission}')
        if running_count >= self.max_commission:
            return SelectedGrids([]), SelectedGrids([])

        # Filter
        COMMISSION_FILTER.load(self.config.Commission_CommissionFilter)
        run = COMMISSION_FILTER.apply(total.grids, func=self._commission_check)
        logger.attr('Filter_sort', ' > '.join([str(c) for c in run]))
        run = SelectedGrids(run)

        # Add shortest
        no_shortest = run.delete(SelectedGrids(['shortest']))
        if no_shortest.count + running_count < self.max_commission:
            if no_shortest.count < run.count:
                logger.info('Not enough commissions to run, add shortest daily commissions')
                COMMISSION_FILTER.load(SHORTEST_FILTER)
                shortest = COMMISSION_FILTER.apply(daily, func=self._commission_check)
                run = no_shortest.add_by_eq(SelectedGrids(shortest))
                logger.attr('Filter_sort', ' > '.join([str(c) for c in run]))
            else:
                logger.info('Not enough commissions to run')

        # Separate daily and urgent
        run = run[:self.max_commission - running_count]
        daily_choose = run.intersect_by_ed(daily)
        urgent_choose = run.intersect_by_ed(urgent)
        if daily_choose:
            logger.info('Choose daily commission')
            for comm in daily_choose:
                logger.info(comm)
        if urgent_choose:
            logger.info('Choose urgent commission')
            for comm in urgent_choose:
                logger.info(comm)

        return daily_choose, urgent_choose

    def _commission_check(self, commission):
        """
        Args:
            commission (Commission):

        Returns:
            bool:
        """
        if not commission.valid or commission.status != 'pending':
            return False
        if not self.config.Commission_DoMajorCommission and commission.category_str == 'major':
            return False

        return True

    def _commission_ensure_mode(self, mode):
        return COMMISSION_SWITCH.set(mode, main=self)

    def _commission_mode_reset(self):
        if self.appear(COMMISSION_DAILY):
            current, another = 'daily', 'urgent'
        elif self.appear(COMMISSION_URGENT):
            current, another = 'urgent', 'daily'
        else:
            logger.warning('Unknown Commission mode')
            return False

        self._commission_ensure_mode(another)
        self._commission_ensure_mode(current)

        return True

    def _commission_swipe(self):
        if COMMISSION_SCROLL.appear(main=self):
            if COMMISSION_SCROLL.at_bottom(main=self):
                return False
            else:
                COMMISSION_SCROLL.next_page(main=self)
                return True
        else:
            return False

    def _commission_swipe_to_top(self):
        if not COMMISSION_SCROLL.appear(main=self):
            return False
        COMMISSION_SCROLL.set_top(main=self, skip_first_screenshot=True)
        return True

    def _commission_scan_list(self):
        """
        Returns:
            SelectedGrids: SelectedGrids containing Commission objects
        """
        commission = SelectedGrids([])
        for _ in range(15):
            new = self._commission_detect(self.device.image)
            commission = commission.add_by_eq(new)

            # End
            if not self._commission_swipe():
                break

        return commission

    def _commission_scan_all(self):
        """
        Pages:
            in: page_commission
            out: page_commission
        """
        logger.hr('Commission scan', level=1)
        # Urgent list is lazy loaded. Check it first for a force update.
        self._commission_ensure_mode('urgent')

        logger.hr('Scan daily', level=2)
        self._commission_ensure_mode('daily')
        self._commission_swipe_to_top()
        daily = self._commission_scan_list()

        logger.hr('Scan urgent', level=2)
        self._commission_ensure_mode('urgent')
        self._commission_swipe_to_top()
        urgent = self._commission_scan_list()
        urgent.call('convert_to_night')  # Convert extra commission to night

        logger.hr('Showing commission', level=2)
        logger.info('Daily commission')
        for comm in daily.sort('status', 'genre'):
            logger.attr('Commission', comm)
        if urgent.count:
            logger.info('Urgent commission')
            for comm in urgent.sort('status', 'genre'):
                logger.attr('Commission', comm)

        self.daily = daily
        self.urgent = urgent
        self.daily_choose, self.urgent_choose = self._commission_choose(self.daily, self.urgent)
        return daily, urgent

    def _commission_start_click(self, comm):
        """
        Start a commission.

        Args:
            comm (Commission):

        Pages:
            in: page_commission
            out: page_commission, info_bar, commission details unfold
        """
        logger.hr(f'Start commission')
        self.interval_clear(COMMISSION_ADVICE)
        self.interval_clear(COMMISSION_START)
        comm_timer = Timer(7)
        count = 0
        while 1:
            if comm_timer.reached():
                self.device.click(comm.button)
                comm_timer.reset()

            if self.handle_popup_confirm():
                comm_timer.reset()
                pass
            if self.appear_then_click(COMMISSION_ADVICE, offset=(5, 20), interval=7):
                count += 1
                comm_timer.reset()
                pass
            if self.appear_then_click(COMMISSION_START, offset=(5, 20), interval=7):
                comm_timer.reset()
                pass

            # End
            if self.info_bar_count():
                break
            if count >= 3:
                # Restart game and handle commission recommend bug.
                # After you click "Recommend", your ships appear and then suddenly disappear.
                # At the same time, the icon of commission is flashing.
                logger.warning('Triggered commission list flashing bug')
                raise GameStuckError('Triggered commission list flashing bug')

            self.device.screenshot()

        return True

    def _commission_find_and_start(self, comm):
        """
        Args:
            comm (Commission):
        """
        logger.hr('Commission find and start')
        logger.info(f'Finding commission {comm}')
        for _ in range(15):
            new = self._commission_detect(self.device.image)
            if comm in new:
                # Update commission position.
                # In different scans, they have the same information, but have different locations.
                for new_comm in new:
                    if comm == new_comm:
                        comm = new_comm
                self._commission_start_click(comm)
                return True

            # End
            if not self._commission_swipe():
                break

        logger.warning(f'Commission not found: {comm}')
        return False

    def commission_start(self):
        """
        Scan and Start all chosen commissions.

        Pages:
            in: page_commission
            out: page_commission
        """
        self._commission_scan_all()

        logger.hr('Commission run', level=1)
        if self.daily_choose:
            for comm in self.daily_choose:
                self._commission_ensure_mode('daily')
                self._commission_swipe_to_top()
                self.handle_info_bar()
                self._commission_find_and_start(comm)
                comm.convert_to_running()
                self._commission_mode_reset()
        if self.urgent_choose:
            for comm in self.urgent_choose:
                self._commission_ensure_mode('urgent')
                self._commission_swipe_to_top()
                self.handle_info_bar()
                self._commission_find_and_start(comm)
                comm.convert_to_running()
                self._commission_mode_reset()
        if not self.daily_choose and not self.urgent_choose:
            logger.info('No commission chose')

    def commission_receive(self, skip_first_screenshot=True):
        """
        Args:
            skip_first_screenshot:

        Returns:
            bool: If rewarded.

        Pages:
            in: page_reward
            out: page_reward
        """
        logger.hr('Reward receive')

        reward = False
        exit_timer = Timer(1, count=3).start()
        click_timer = Timer(1)
        with self.stat.new('commission',
                           save=self.config.DropRecord_SaveCommission,
                           upload=self.config.DropRecord_UploadCommission) as drop:
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                for button in [EXP_INFO_S_REWARD, GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP]:
                    if self.appear(button, interval=1):
                        self.ensure_no_info_bar(timeout=1)
                        drop.add(self.device.image)

                        REWARD_SAVE_CLICK.name = button.name
                        self.device.click(REWARD_SAVE_CLICK)
                        click_timer.reset()
                        exit_timer.reset()
                        reward = True
                        continue
                if click_timer.reached() and self.appear_then_click(REWARD_1, interval=1):
                    exit_timer.reset()
                    click_timer.reset()
                    reward = True
                    continue
                if not self.appear(page_reward.check_button) or self.info_bar_count():
                    exit_timer.reset()
                    continue

                # End
                if exit_timer.reached():
                    break

        return reward

    def run(self):
        """
        Pages:
            in: Any
            out: page_commission
        """
        self.ui_ensure(page_reward)
        self.commission_receive()

        self.ui_goto(page_commission, skip_first_screenshot=True)
        # info_bar appears when get ship in Launch Ceremony commissions
        # This is a game bug, the info_bar shows get ship, will appear over and over again, until you click get_ship.
        self.handle_info_bar()
        self.commission_start()

        total = self.daily.add_by_eq(self.urgent)
        future_finish = sorted([f for f in total.get('finish_time') if f is not None])
        logger.info(f'Commission finish: {[str(f) for f in future_finish]}')
        if len(future_finish):
            self.config.delay_next_run(target=future_finish)
        else:
            logger.info('No commission running')
            self.config.delay_next_run(success=False)